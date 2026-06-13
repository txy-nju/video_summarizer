"""
上传会话持久化 Repository（Redis 后端）。

使用 Redis Hash 存储每个 upload session 的元数据，Key 格式：
    upload:session:{upload_id}

分片文件存储在本地临时目录：
    temp/uploads/{upload_id}/chunk_{index}

TTL：会话默认 24 小时后过期（可配置）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis

from backend.schemas.upload import UploadSessionState

logger = logging.getLogger(__name__)

# 默认会话 TTL：24 小时
_DEFAULT_SESSION_TTL_SECONDS = 86400
# 分片存储根目录（本地开发模式；生产替换为 OSS 临时存储）
_UPLOAD_CHUNK_ROOT = Path(__file__).resolve().parent.parent.parent / "temp" / "uploads"


class UploadRepository:
    """管理上传会话的元数据（Redis）与分片文件（本地磁盘）。"""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client
        _UPLOAD_CHUNK_ROOT.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    def create_session(
        self,
        upload_id: str,
        owner_id: str,
        file_name: str,
        total_size: int,
        chunk_size: int,
        ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    ) -> UploadSessionState:
        """创建新的上传会话，写入 Redis。"""
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)

        state = UploadSessionState(
            upload_id=upload_id,
            owner_id=owner_id,
            file_name=file_name,
            total_size=total_size,
            chunk_size=chunk_size,
            uploaded_chunks=set(),
            state="created",
            expires_at=expires_at.isoformat(),
            created_at=now.isoformat(),
            video_id=None,
        )

        self._save(state, ttl_seconds)
        logger.info("Upload session created: upload_id=%s, total_size=%d", upload_id, total_size)
        return state

    def get_session(self, upload_id: str) -> UploadSessionState | None:
        """查询上传会话状态。"""
        key = self._session_key(upload_id)
        raw = self._redis.hgetall(key)
        if not raw:
            return None
        return self._deserialize(raw)

    def mark_chunk_uploaded(self, upload_id: str, chunk_index: int) -> UploadSessionState | None:
        """记录一个分片已完成上传。"""
        state = self.get_session(upload_id)
        if state is None:
            return None

        state.uploaded_chunks.add(chunk_index)
        if state.state == "created":
            state.state = "uploading"

        self._save(state)
        return state

    def update_state(self, upload_id: str, new_state: str) -> UploadSessionState | None:
        """更新会话状态（如 uploading_complete / finalizing / done / failed / cancelled）。"""
        state = self.get_session(upload_id)
        if state is None:
            return None
        state.state = new_state
        self._save(state)
        return state

    def set_video_id(self, upload_id: str, video_id: str) -> UploadSessionState | None:
        """Write video_id into the session without changing its state.

        Used by the Celery finalize task to immediately persist the created
        video_id so that retries can discover the existing row instead of
        creating duplicates. Does NOT alter the session state.
        """
        state = self.get_session(upload_id)
        if state is None:
            return None
        state.video_id = video_id
        self._save(state)
        return state

    def finalize_session(
        self,
        upload_id: str,
        *,
        video_id: str | None = None,
        final_state: str = "done",
    ) -> UploadSessionState | None:
        """更新会话的 video_id 和终态状态（原子操作）。

        用于上传最终化完成后写入正确的 video_id（去重复用 or 新建），
        使得前端通过 GET /api/v1/uploads/{upload_id} 能获取到最终结果。
        """
        state = self.get_session(upload_id)
        if state is None:
            return None
        if video_id is not None:
            state.video_id = video_id
        state.state = final_state
        self._save(state)
        return state

    def delete_session(self, upload_id: str) -> bool:
        """删除会话元数据（不清除已合并的最终文件）。"""
        key = self._session_key(upload_id)
        deleted = self._redis.delete(key)
        return deleted > 0

    # ------------------------------------------------------------------
    # 分片文件 I/O
    # ------------------------------------------------------------------

    def chunk_dir(self, upload_id: str) -> Path:
        """获取指定上传会话的分片存储目录。"""
        d = _UPLOAD_CHUNK_ROOT / upload_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_chunk(self, upload_id: str, chunk_index: int, data: bytes) -> Path:
        """将分片数据写入磁盘。"""
        chunk_path = self.chunk_dir(upload_id) / f"chunk_{chunk_index:06d}"
        chunk_path.write_bytes(data)
        return chunk_path

    def chunk_exists(self, upload_id: str, chunk_index: int) -> bool:
        """检查指定分片文件是否存在。"""
        return (self.chunk_dir(upload_id) / f"chunk_{chunk_index:06d}").exists()

    def merge_chunks(self, upload_id: str, total_chunks: int) -> Path:
        """按顺序合并所有分片为单一文件。

        Returns:
            合并后文件的 Path（在 upload chunk 目录下）。
        """
        output_path = self.chunk_dir(upload_id) / "merged"
        with output_path.open("wb") as out:
            for i in range(total_chunks):
                chunk_path = self.chunk_dir(upload_id) / f"chunk_{i:06d}"
                if not chunk_path.exists():
                    raise FileNotFoundError(f"Missing chunk {i} for upload_id={upload_id}")
                out.write(chunk_path.read_bytes())
        logger.info("Chunks merged for upload_id=%s: %d chunks", upload_id, total_chunks)
        return output_path

    def cleanup_chunks(self, upload_id: str) -> None:
        """清理分片文件（合并完成后调用）。"""
        import shutil

        chunk_dir = self.chunk_dir(upload_id)
        for f in chunk_dir.glob("chunk_*"):
            f.unlink(missing_ok=True)

    def get_merged_path(self, upload_id: str) -> Path:
        """获取合并后文件的路径。"""
        return self.chunk_dir(upload_id) / "merged"

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _session_key(upload_id: str) -> str:
        return f"upload:session:{upload_id}"

    def _save(self, state: UploadSessionState, ttl_seconds: int | None = None) -> None:
        key = self._session_key(state.upload_id)
        payload = {
            "upload_id": state.upload_id,
            "owner_id": state.owner_id,
            "file_name": state.file_name,
            "total_size": str(state.total_size),
            "chunk_size": str(state.chunk_size),
            "uploaded_chunks": json.dumps(sorted(state.uploaded_chunks)),
            "state": state.state,
            "expires_at": state.expires_at,
            "created_at": state.created_at,
            "video_id": state.video_id or "",
        }
        self._redis.hset(key, mapping=payload)
        if ttl_seconds is None:
            # 保留剩余 TTL
            remaining = self._redis.ttl(key)
            if remaining > 0:
                ttl_seconds = remaining
            else:
                ttl_seconds = _DEFAULT_SESSION_TTL_SECONDS
        self._redis.expire(key, ttl_seconds)

    @staticmethod
    def _deserialize(raw: dict[bytes, bytes]) -> UploadSessionState:
        def _b(key: str) -> str:
            val = raw.get(key.encode()) or raw.get(key)
            if isinstance(val, bytes):
                return val.decode()
            return str(val) if val is not None else ""

        chunks_raw = _b("uploaded_chunks")
        chunks = set(json.loads(chunks_raw)) if chunks_raw else set()

        video_id_raw = _b("video_id")
        return UploadSessionState(
            upload_id=_b("upload_id"),
            owner_id=_b("owner_id"),
            file_name=_b("file_name"),
            total_size=int(_b("total_size") or "0"),
            chunk_size=int(_b("chunk_size") or str(10 * 1024 * 1024)),
            uploaded_chunks=chunks,
            state=_b("state") or "created",
            expires_at=_b("expires_at"),
            created_at=_b("created_at"),
            video_id=video_id_raw if video_id_raw else None,
        )
