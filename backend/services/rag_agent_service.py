"""RAG Agent Service：基于 MODULAR-RAG-MCP-SERVER 核心能力的真实检索回答服务。"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RagAgentAnswer:
    answer_content: str
    cited_sources: list[dict]


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
        collection = f"video_{task_id}"
        answer = self._rag_answer(
            question=question_content,
            collection=collection,
            top_k=5,
            rerank=True,
        )
        for i in range(0, len(answer.answer_content), 64):
            yield answer.answer_content[i : i + 64]

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
    ) -> tuple[RagAgentAnswer, Iterator[str]]:
        answer = self.answer_global_question(
            owner_id=owner_id,
            kbid=kbid,
            question_content=question_content,
            attachments=attachments,
            kb_config=kb_config,
        )

        def _gen() -> Iterator[str]:
            for i in range(0, len(answer.answer_content), 64):
                yield answer.answer_content[i : i + 64]

        return answer, _gen()

    # ── 核心 RAG 调用（多模态版）──────────────────────────────────────

    def _rag_answer(
        self,
        *,
        question: str,
        collection: str,
        top_k: int,
        rerank: bool,
    ) -> RagAgentAnswer:
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

        # ── 帧匹配：通过已有 keyframes 元数据按时间戳匹配，零视频解析开销 ──
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

        # ── 答案生成：有帧走多模态，无帧走纯文本 ──────────────────────────
        if frames:
            try:
                answer_text = self._call_multimodal_llm(
                    question=question,
                    results=results,
                    frames=frames,
                    settings=settings,
                )
            except Exception as exc:
                logger.warning("multimodal LLM failed, falling back to text-only: %s", exc)
                answer_text = self._build_text_answer(results, question, fallback_reason)
        else:
            answer_text = self._build_text_answer(results, question, fallback_reason)

        # ── 构造 cited_sources ────────────────────────────────────────────
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

        return RagAgentAnswer(answer_content=answer_text, cited_sources=cited)

    def _build_text_answer(
        self,
        results: list,
        question: str,
        fallback_reason: str | None,
    ) -> str:
        from modular_rag.core.response.response_builder import ResponseBuilder
        builder = ResponseBuilder()
        payload = builder.build(
            retrieval_results=results,
            query=question,
            fallback_reason=fallback_reason,
        )
        text_blocks = [
            c["text"] for c in payload.get("content", []) if c.get("type") == "text"
        ]
        return "\n".join(text_blocks)

    def _call_multimodal_llm(
        self,
        *,
        question: str,
        results: list,
        frames: list[dict],
        settings,
    ) -> str:
        """将文本 chunks + 帧图像一同送入多模态 LLM（GPT-4o Vision）。"""
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.api_url,
        )
        text_context = "\n\n".join(r.text for r in results if r.text)
        content: list[dict] = [
            {
                "type": "text",
                "text": f"请基于以下视频转录内容和对应视频帧回答问题。\n\n转录内容：\n{text_context}",
            }
        ]
        for f in frames:
            try:
                with open(f["frame_path"], "rb") as img_file:
                    b64 = base64.b64encode(img_file.read()).decode()
                label = f.get("time_range", "")
                if label:
                    content.append({"type": "text", "text": f"视频帧（{label}）："})
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })
            except OSError:
                continue
        content.append({"type": "text", "text": f"\n问题：{question}"})

        response = client.chat.completions.create(
            model=settings.llm.model,
            messages=[{"role": "user", "content": content}],
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""

    # ── 工具方法 ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_kb_collection(kbid: str) -> str:
        """从数据库查询 KB 的 vector_collection_name。"""
        from backend.db.session import SessionLocal
        from backend.repositories.kb_repository import KnowledgeBaseRepository

        db = SessionLocal()
        try:
            repo = KnowledgeBaseRepository(db_session=db)
            kb = repo.get_by_id(kbid)
            return kb.vector_collection_name if kb and kb.vector_collection_name else f"kb_{kbid}"
        finally:
            db.close()
