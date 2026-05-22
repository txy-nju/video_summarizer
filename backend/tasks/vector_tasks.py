"""向量化任务：将转录文本分块嵌入写入向量库（步骤 7 实现）。"""
from __future__ import annotations

import logging

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="backend.tasks.vector_tasks.async_embed_transcript_chunks_background",
    acks_late=True,
    queue="low_priority",
)
def async_embed_transcript_chunks_background(video_id: str, trace_id: str = "") -> dict:
    """
    后台向量化任务：
    1. 读取 video_resources.full_transcript 和 transcript_segments
    2. 优先使用 segments（带时间戳）；无 segments 则降级为全文本
    3. 通过 IngestionPipeline 分块 + 嵌入 + 写入 Chroma + 构建 BM25 索引
    chunk metadata.collection = "video_{video_id}"（数据隔离键）
    """
    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository

    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db_session=db)
        video = repo.get_by_id_system(video_id)
        if video is None:
            logger.warning(
                "async_embed_transcript_chunks_background: video_id=%s not found", video_id
            )
            return {"video_id": video_id, "status": "NOT_FOUND"}

        transcript = video.full_transcript or ""
        if not transcript.strip():
            logger.info(
                "async_embed_transcript_chunks_background: video_id=%s empty transcript, skip",
                video_id,
            )
            return {"video_id": video_id, "status": "SKIPPED", "reason": "empty transcript"}

        collection = f"video_{video_id}"
        base_metadata = {
            "source_path": f"transcript://video/{video_id}",
            "video_id": video_id,
            "owner_id": video.owner_id,
            "doc_type": "transcript",
        }

        segments = video.transcript_segments or []
        if segments:
            _run_rag_ingestion_with_segments(
                segments=segments,
                collection=collection,
                base_metadata=base_metadata,
            )
        else:
            _run_rag_ingestion(
                text=transcript,
                collection=collection,
                metadata={**base_metadata, "images": []},
            )

        logger.info(
            "async_embed_transcript_chunks_background: video_id=%s collection=%s segments=%d ingested",
            video_id, collection, len(segments),
        )
        return {
            "video_id": video_id,
            "status": "COMPLETED",
            "collection": collection,
            "use_segments": bool(segments),
        }
    finally:
        db.close()


def _run_rag_ingestion_with_segments(
    segments: list[dict],
    collection: str,
    base_metadata: dict,
) -> None:
    """基于 Whisper segments 进行时间戳感知摄取。
    每个 segment 生成独立 Document，metadata 携带 start_s/end_s/time_range。
    """
    from backend.infrastructure.rag_settings_factory import build_rag_settings
    from modular_rag.ingestion.pipeline import IngestionPipeline
    from modular_rag.libs.loader.transcript_text_loader import TranscriptTextLoader

    loader = TranscriptTextLoader()
    docs = loader.load_segments(segments=segments, base_metadata=base_metadata)
    if not docs:
        return

    settings = build_rag_settings()
    pipeline = IngestionPipeline(settings=settings, loader=loader)
    pipeline.run_docs(docs=docs, collection=collection)


def _run_rag_ingestion(text: str, collection: str, metadata: dict) -> None:
    """全文本摄取（无时间戳），用于 segments 缺失时的降级路径。
    同样被 global_retrieval_tasks.py 复用。
    """
    from backend.infrastructure.rag_settings_factory import build_rag_settings
    from modular_rag.ingestion.pipeline import IngestionPipeline
    from modular_rag.libs.loader.transcript_text_loader import TranscriptTextLoader

    loader = TranscriptTextLoader()
    docs = loader.load_text(text, metadata=metadata)
    if not docs:
        return

    settings = build_rag_settings()
    pipeline = IngestionPipeline(settings=settings, loader=loader)
    pipeline.run_docs(docs=docs, collection=collection)

