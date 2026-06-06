"""RAG Agent Service：基于 MODULAR-RAG-MCP-SERVER 核心能力的真实检索回答服务。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RagAgentAnswer:
    answer_content: str
    cited_sources: list[dict]


@dataclass(slots=True)
class _RagContext:
    """检索阶段的完整结果，供后续 LLM 调用使用（与 LLM 无关，可复用）。"""
    results: list
    frames: list[dict]
    fallback_reason: str | None
    cited_sources: list[dict]
    settings: Any



class RagAgentService:
    """使用 MODULAR-RAG-MCP-SERVER HybridSearch 实现真实 RAG 检索。"""

    # ── 单视频 QA ──────────────────────────────────────────────────

    def stream_video_question(
        self,
        *,
        owner_id: str,
        task_id: str,
        question_content: str,
        attachments: list[dict],
    ) -> Iterator[str]:
        """真实 LLM token 流：先检索再流式生成，token 到达即 yield。"""
        collection = self._resolve_video_collection(task_id)
        context = self._build_retrieval_context(question_content, collection, top_k=5, rerank=True)
        context.frames.extend(self._download_attachment_frames(attachments))
        yield from self._stream_from_context(question_content, context)

    # ── 全局 KB QA ─────────────────────────────────────────────────

    def answer_global_question(
        self,
        *,
        owner_id: str,
        kbid: str,
        question_content: str,
        attachments: list[dict],
        kb_config: dict | None = None,
    ) -> RagAgentAnswer:
        collection = self._resolve_kb_collection(kbid)
        cfg = (kb_config or {}).get("retrieval", {})
        return self._rag_answer(
            question=question_content,
            collection=collection,
            top_k=int(cfg.get("top_k", 6)),
            rerank=bool(cfg.get("rerank", True)),
            extra_frames=self._download_attachment_frames(attachments),
            is_kb=True,
            kbid=kbid,
        )

    def stream_global_question(
        self,
        *,
        owner_id: str,
        kbid: str,
        question_content: str,
        attachments: list[dict],
        kb_config: dict | None = None,
    ) -> tuple[list[dict], Iterator[str]]:
        """返回 (cited_sources, token_gen)。
        cited_sources 在检索完成后立即可用；token_gen 是真实 LLM token 流。
        调用方负责在 token_gen 耗尽后将完整答案持久化到数据库。
        """
        collection = self._resolve_kb_collection(kbid)
        cfg = (kb_config or {}).get("retrieval", {})
        context = self._build_retrieval_context(
            question_content, collection,
            top_k=int(cfg.get("top_k", 6)),
            rerank=bool(cfg.get("rerank", True)),
            is_kb=True,
            kbid=kbid,
        )
        context.frames.extend(self._download_attachment_frames(attachments))
        return context.cited_sources, self._stream_from_context(question_content, context)

    # ── 核心非流式路径（answer_global_question 使用）─────────────────

    def _rag_answer(self, *, question: str, collection: str, top_k: int, rerank: bool, extra_frames: list[dict] | None = None, is_kb: bool = False, kbid: str = "") -> RagAgentAnswer:
        context = self._build_retrieval_context(question, collection, top_k, rerank, is_kb=is_kb, kbid=kbid)
        if extra_frames:
            context.frames.extend(extra_frames)
        answer_text = "".join(self._stream_from_context(question, context))
        return RagAgentAnswer(answer_content=answer_text, cited_sources=context.cited_sources)

    # ── 检索阶段（HybridSearch + Reranker + KeyframeLookup）──────────

    def _build_retrieval_context(
        self,
        question: str,
        collection: str,
        top_k: int,
        rerank: bool,
        is_kb: bool = False,
        kbid: str = "",
    ) -> _RagContext:
        from modular_rag.core.query_engine.hybrid_search import HybridSearch
        from modular_rag.core.query_engine.reranker import Reranker
        from backend.infrastructure.rag_settings_factory import build_rag_settings, _BM25_INDEX_DIR
        from backend.infrastructure.keyframe_lookup import KeyframeLookup, load_keyframes_for_video
        from pathlib import Path

        if is_kb and kbid:
            bm25_dir = str(Path(_BM25_INDEX_DIR) / f"kb_{kbid}")
            settings = build_rag_settings(collection=collection, bm25_index_dir=bm25_dir)
            filters = None  # 物理隔离：collection 即 Chroma physical collection，无需 metadata filter
        else:
            settings = build_rag_settings()  # 视频 QA 保持共享 "default" collection
            filters = {"collection": collection}  # 逻辑隔离：metadata 过滤

        hybrid = HybridSearch(settings=settings)

        logger.info(
            "RAG retrieval start: collection=%s top_k=%s question_len=%s "
            "chroma_path=%s",
            collection, top_k, len(question),
            getattr(settings.vector_store, "persist_path", "N/A"),
        )

        results = hybrid.search(
            query=question,
            top_k=top_k,
            filters=filters,
        )

        result_count = len(results)
        logger.info(
            "RAG retrieval done: collection=%s top_k=%s result_count=%s",
            collection, top_k, result_count,
        )

        # 零结果诊断：直接查询 Chroma 确认该 collection 下是否有数据
        if result_count == 0:
            logger.warning(
                "RAG retrieval ZERO results: collection=%s top_k=%s "
                "chroma_path=%s → Running direct Chroma probe...",
                collection, top_k,
                getattr(settings.vector_store, "persist_path", "N/A"),
            )
            try:
                from modular_rag.libs.vector_store.chroma_store import ChromaStore
                store = ChromaStore.from_settings(settings.vector_store)
                probe_results = store.get_by_metadata({"collection": collection}, limit=5)
                total_stats = store.get_collection_stats()
                if probe_results:
                    sample_meta = probe_results[0].metadata or {}
                    logger.warning(
                        "RAG ZERO-RESULT DIAG: collection=%s chroma_has_data=YES "
                        "probe_count=%d total_chunks=%s sample_meta_keys=%s "
                        "→ Data EXISTS but retrieval filter didn't match! "
                        "Check filter normalization.",
                        collection, len(probe_results),
                        total_stats.get("chunk_count", -1),
                        list(sample_meta.keys()),
                    )
                else:
                    logger.error(
                        "RAG ZERO-RESULT DIAG: collection=%s chroma_has_data=NO "
                        "probe_count=0 total_chunks=%s chroma_path=%s "
                        "→ No data in Chroma for this collection! "
                        "Vectors were never written or written to a different path.",
                        collection, total_stats.get("chunk_count", -1),
                        getattr(settings.vector_store, "persist_path", "N/A"),
                    )
            except Exception:
                logger.exception(
                    "RAG ZERO-RESULT DIAG: Chroma probe failed for collection=%s",
                    collection,
                )

        fallback_reason: str | None = None
        if rerank and results:
            reranker = Reranker(settings=settings)
            rerank_result = reranker.rerank(query=question, candidates=results)
            results = rerank_result.candidates
            if rerank_result.fallback:
                fallback_reason = rerank_result.fallback_reason

        _kf_cache: dict[str, list[dict]] = {}
        frames: list[dict] = []
        for r in results:
            meta = r.metadata or {}
            start_s = meta.get("start_s")
            vid = meta.get("video_id")
            if start_s is None or not vid:
                continue
            if vid not in _kf_cache:
                _kf_cache[vid] = load_keyframes_for_video(vid)
            kf = KeyframeLookup.find_nearest(
                video_id=vid,
                timestamp_s=float(start_s),
                keyframes=_kf_cache[vid],
            )
            if kf:
                frame_path = KeyframeLookup.download_frame(kf)
                if frame_path:
                    frames.append({
                        "video_id": vid,
                        "frame_path": frame_path,
                        "time_range": str(meta.get("time_range", "")),
                    })

        cited: list[dict] = []
        for r in results:
            meta = r.metadata or {}
            cited.append({
                "video_id": str(meta.get("video_id", "")),
                "task_id": meta.get("video_id"),
                "time_range": str(meta.get("time_range", "")),
                "quote": r.text[:200],
                "score": min(max(float(r.score), 0.0), 1.0),
            })

        return _RagContext(
            results=results,
            frames=frames,
            fallback_reason=fallback_reason,
            cited_sources=cited,
            settings=settings,
        )

    # ── 附件下载 ─────────────────────────────────────────────────────

    @staticmethod
    def _download_attachment_frames(attachments: list[dict]) -> list[dict]:
        """将用户上传的图片附件从 OSS 下载到本地缓存，返回 frames 格式列表。

        仅处理 mime_type 以 'image/' 开头的附件，其余类型跳过。
        缓存路径：temp/frames/attachments/<oss_key 转义名>；已存在时直接复用。
        """
        if not attachments:
            return []

        import shutil
        from pathlib import Path
        from backend.infrastructure.storage.oss_client import get_object_storage_client

        cache_dir = Path("temp/frames/attachments")
        cache_dir.mkdir(parents=True, exist_ok=True)
        storage = get_object_storage_client()
        frames: list[dict] = []

        for att in attachments:
            mime_type: str = att.get("mime_type", "")
            oss_key: str = att.get("oss_key", "").strip()
            name: str = att.get("name", oss_key)

            if not mime_type.startswith("image/") or not oss_key:
                continue

            # 用 oss_key 转义为单层文件名，避免路径分隔符冲突
            sanitized = oss_key.replace("/", "_").replace("\\", "_")
            cache_path = cache_dir / sanitized
            try:
                if not cache_path.exists():
                    with storage.materialize_to_local_path(oss_key) as tmp_path:
                        shutil.copy2(tmp_path, cache_path)
                frames.append({
                    "frame_path": str(cache_path),
                    "time_range": name,
                    "mime_type": mime_type,
                })
            except Exception:
                logger.warning("_download_attachment_frames: 跳过附件 oss_key=%s", oss_key)

        return frames

    # ── LLM 流式生成 ────────────────────────────────────────────────

    def _stream_from_context(self, question: str, context: _RagContext) -> Iterator[str]:
        """根据是否有帧分派到多模态或纯文本流式 LLM。"""
        from core.llm.rag_llm import RagStreamLLM

        llm = RagStreamLLM.from_rag_settings(context.settings)
        if context.frames:
            try:
                yield from llm.stream_multimodal(
                    question=question,
                    results=context.results,
                    frames=context.frames,
                )
                return
            except Exception as exc:
                logger.warning("multimodal LLM streaming failed, falling back to text: %s", exc)
        yield from llm.stream_text(question=question, results=context.results)

    # ── 工具方法 ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_video_collection(task_id: str) -> str:
        """从 task_id 反查 video_id，构建向量库 collection 名。

        向量化任务以 video_{video_id} 命名 collection，而 RAG 调用方只有 task_id。
        此方法通过查库获取 video_id，确保检索目标与写入目标一致。
        """
        from backend.db.session import SessionLocal
        from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository

        db = SessionLocal()
        try:
            repo = VideoSummaryTaskRepository(db_session=db)
            task = repo.get_by_id_system(task_id)
            if task and task.video_id:
                return f"video_{task.video_id}"
        finally:
            db.close()
        return f"video_{task_id}"

    @staticmethod
    def _resolve_kb_collection(kbid: str) -> str:
        """从数据库查询 KB 的 vector_collection_name。"""
        from backend.db.session import SessionLocal
        from backend.repositories.kb_repository import KnowledgeBaseRepository

        db = SessionLocal()
        try:
            repo = KnowledgeBaseRepository(db_session=db)
            kb = repo.get_by_id_system(kbid)
            return kb.vector_collection_name if kb and kb.vector_collection_name else f"kb_{kbid}"
        finally:
            db.close()
