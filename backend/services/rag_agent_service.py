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
        collection = f"video_{task_id}"
        context = self._build_retrieval_context(question_content, collection, top_k=5, rerank=True)
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
        )
        return context.cited_sources, self._stream_from_context(question_content, context)

    # ── 核心非流式路径（answer_global_question 使用）─────────────────

    def _rag_answer(self, *, question: str, collection: str, top_k: int, rerank: bool) -> RagAgentAnswer:
        context = self._build_retrieval_context(question, collection, top_k, rerank)
        answer_text = "".join(self._stream_from_context(question, context))
        return RagAgentAnswer(answer_content=answer_text, cited_sources=context.cited_sources)

    # ── 检索阶段（HybridSearch + Reranker + KeyframeLookup）──────────

    def _build_retrieval_context(
        self,
        question: str,
        collection: str,
        top_k: int,
        rerank: bool,
    ) -> _RagContext:
        from modular_rag.core.query_engine.hybrid_search import HybridSearch
        from modular_rag.core.query_engine.reranker import Reranker
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        from backend.infrastructure.keyframe_lookup import KeyframeLookup, load_keyframes_for_video

        settings = build_rag_settings()
        hybrid = HybridSearch(settings=settings)
        results = hybrid.search(
            query=question,
            top_k=top_k,
            filters={"collection": collection},
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
