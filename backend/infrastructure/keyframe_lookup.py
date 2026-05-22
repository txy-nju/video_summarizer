"""根据 chunk 时间戳匹配数据库中已提取的关键帧，通过 OSS 下载供多模态 RAG 使用。"""
from __future__ import annotations

import shutil
from pathlib import Path

# 时间匹配窗口（秒）：chunk 的 start_s 前后各 N 秒内查找最近关键帧
FRAME_MATCH_WINDOW_SECONDS = 5.0


class KeyframeLookup:
    """
    不解析视频，不调用 ffmpeg。
    从 video_resources.keyframes 元数据中按时间戳匹配最近帧，
    再通过 OSS 下载帧图像到本地缓存目录。
    """

    @staticmethod
    def find_nearest(
        video_id: str,
        timestamp_s: float,
        keyframes: list[dict],
    ) -> dict | None:
        """
        在已有 keyframes 列表中查找时间窗口内最近的帧。

        Args:
            video_id: 视频 ID（仅在日志中使用）。
            timestamp_s: chunk.metadata["start_s"]（秒，浮点数）。
            keyframes: VideoResource.keyframes 列表，
                       每项格式 {"time": "MM:SS", "oss_key": "frames/..."}。

        Returns:
            匹配到的 keyframe dict，未匹配返回 None。
        """
        if not keyframes:
            return None

        best: dict | None = None
        best_diff = float("inf")
        for kf in keyframes:
            kf_time_s = KeyframeLookup._time_str_to_seconds(str(kf.get("time", "")))
            diff = abs(kf_time_s - timestamp_s)
            if diff <= FRAME_MATCH_WINDOW_SECONDS and diff < best_diff:
                best = kf
                best_diff = diff
        return best

    @staticmethod
    def download_frame(keyframe: dict) -> str | None:
        """
        通过 OSS 将匹配到的关键帧下载到本地缓存目录。
        缓存目录：temp/frames/rag/；已存在则复用，不重复下载。

        Returns:
            本地缓存文件路径字符串，下载失败返回 None。
        """
        oss_key = str(keyframe.get("oss_key", "")).strip()
        if not oss_key:
            return None
        try:
            from backend.infrastructure.storage.oss_client import get_object_storage_client
            cache_dir = Path("temp/frames/rag")
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / Path(oss_key).name
            if cache_path.exists():
                return str(cache_path)
            storage = get_object_storage_client()
            with storage.materialize_to_local_path(oss_key) as tmp_path:
                shutil.copy2(tmp_path, cache_path)
            return str(cache_path)
        except Exception:
            return None

    @staticmethod
    def _time_str_to_seconds(t: str) -> float:
        """将 'MM:SS' 或 'HH:MM:SS' 转为秒数。"""
        parts = t.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except (ValueError, IndexError):
            pass
        return 0.0


def load_keyframes_for_video(video_id: str) -> list[dict]:
    """从数据库加载指定视频的 keyframes 元数据列表。"""
    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository

    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db_session=db)
        video = repo.get_by_id_system(video_id)
        if video is None:
            return []
        kf = video.keyframes
        return kf if isinstance(kf, list) else []
    finally:
        db.close()
