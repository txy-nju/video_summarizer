"""向量化任务：将转录文本分块嵌入写入向量库（步骤 7 实现）。"""
from __future__ import annotations

import logging

from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    base=BaseTask,          # ← 加这一行
    name="backend.tasks.vector_tasks.async_embed_transcript_chunks_background",
    acks_late=True,
    queue="low_priority",
    max_retries=3,
    default_retry_delay=30,
    task_soft_time_limit=600,
    task_time_limit=900,
)
def async_embed_transcript_chunks_background(self, video_id: str, trace_id: str = "") -> dict:
    """
    后台向量化任务：
    1. 读取 video_resources.full_transcript 和 transcript_segments
    2. 优先使用 segments（带时间戳）；无 segments 则降级为全文本
    3. 通过 IngestionPipeline 分块 + 嵌入 + 写入 Chroma + 构建 BM25 索引
    chunk metadata.collection = "video_{video_id}"（数据隔离键）

    幂等性保护：通过 _is_collection_indexed 检查 Chroma 中是否已有该视频的向量数据，
    若已存在则跳过摄取，防止重试时的重复写入。
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

        if _is_collection_indexed(collection):
            logger.info(
                "async_embed_transcript_chunks_background: video_id=%s already indexed, skip",
                video_id,
            )
            return {"video_id": video_id, "status": "ALREADY_INDEXED", "collection": collection}

        base_metadata = {
            "source_path": f"transcript://video/{video_id}",
            "video_id": video_id,
            "owner_id": video.owner_id,
            "doc_type": "transcript",
        }

        from backend.infrastructure.rag_settings_factory import build_rag_settings
        _settings = build_rag_settings()
        _chroma_path = getattr(_settings.vector_store, "persist_path", "N/A")

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
            "async_embed_transcript_chunks_background: video_id=%s collection=%s segments=%d "
            "ingested chroma_path=%s",
            video_id, collection, len(segments), _chroma_path,
        )
        return {
            "video_id": video_id,
            "status": "COMPLETED",
            "collection": collection,
            "use_segments": bool(segments),
        }
    except Exception as exc:
        logger.exception(
            "async_embed_transcript_chunks_background failed for video_id=%s trace_id=%s",
            video_id, trace_id,
        )
        if self.request.retries >= self.max_retries:
            logger.critical(
                "async_embed_transcript_chunks_background: retries exhausted for video_id=%s trace_id=%s",
                video_id, trace_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
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

    settings = build_rag_settings()
    chroma_path = getattr(settings.vector_store, "persist_path", "N/A")
    video_id = base_metadata.get("video_id", "unknown")

    loader = TranscriptTextLoader()
    docs = loader.load_segments(segments=segments, base_metadata=base_metadata)
    if not docs:
        logger.warning(
            "_run_rag_ingestion_with_segments: no docs from segments, collection=%s video_id=%s",
            collection, video_id,
        )
        return

    logger.info(
        "_run_rag_ingestion_with_segments: starting collection=%s video_id=%s "
        "segments=%d docs=%d chroma_path=%s",
        collection, video_id, len(segments), len(docs), chroma_path,
    )

    pipeline = IngestionPipeline(settings=settings, loader=loader)
    pipeline.run_docs(docs=docs, collection=collection)

    _verify_ingestion(collection, video_id, chroma_path)


def _run_rag_ingestion(text: str, collection: str, metadata: dict) -> None:
    """全文本摄取（无时间戳），用于 segments 缺失时的降级路径。
    同样被 global_retrieval_tasks.py 复用。
    """
    from backend.infrastructure.rag_settings_factory import build_rag_settings
    from modular_rag.ingestion.pipeline import IngestionPipeline
    from modular_rag.libs.loader.transcript_text_loader import TranscriptTextLoader

    settings = build_rag_settings()
    chroma_path = getattr(settings.vector_store, "persist_path", "N/A")
    video_id = metadata.get("video_id", "unknown")

    loader = TranscriptTextLoader()
    docs = loader.load_text(text, metadata=metadata)
    if not docs:
        logger.warning(
            "_run_rag_ingestion: no docs from text, collection=%s video_id=%s",
            collection, video_id,
        )
        return

    logger.info(
        "_run_rag_ingestion: starting collection=%s video_id=%s "
        "text_len=%d docs=%d chroma_path=%s",
        collection, video_id, len(text), len(docs), chroma_path,
    )

    pipeline = IngestionPipeline(settings=settings, loader=loader)
    pipeline.run_docs(docs=docs, collection=collection)

    _verify_ingestion(collection, video_id, chroma_path)


def _is_collection_indexed(collection: str) -> bool:
    """检查 Chroma 中是否已存在属于该逻辑 collection 的向量数据。
    用 limit=1 仅探测首条记录，避免加载全量数据，开销极低。
    """
    try:
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        settings = build_rag_settings()
        store = ChromaStore.from_settings(settings.vector_store)
        results = store.get_by_metadata({"collection": collection}, limit=1)
        return len(results) > 0
    except Exception:
        logger.exception("_is_collection_indexed: check failed for collection=%s, will re-ingest", collection)
        return False


def _verify_ingestion(collection: str, video_id: str, chroma_path: str) -> None:
    """验证向量数据是否已成功写入 Chroma。

    摄取完成后立即查询 Chroma，确认对应 collection + video_id 的数据存在。
    若查询结果为空，输出详细诊断信息帮助定位问题。
    """
    try:
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        settings = build_rag_settings()
        actual_path = getattr(settings.vector_store, "persist_path", "N/A")
        store = ChromaStore.from_settings(settings.vector_store)

        # 用 collection 过滤查询（与检索路径完全一致的过滤条件）
        results = store.get_by_metadata({"collection": collection}, limit=5)
        total_count = store.get_collection_stats().get("chunk_count", -1)

        if results:
            sample_meta = results[0].metadata or {}
            logger.info(
                "_verify_ingestion: OK collection=%s video_id=%s "
                "found_chunks=%d total_chroma_chunks=%s chroma_path=%s "
                "sample_chunk_id=%s sample_meta_keys=%s",
                collection, video_id, len(results), total_count, actual_path,
                results[0].id, list(sample_meta.keys()),
            )
        else:
            logger.error(
                "_verify_ingestion: FAILED collection=%s video_id=%s "
                "found_chunks=0 total_chroma_chunks=%s chroma_path=%s "
                "→ Vectors may NOT be persisted! Check if Celery worker "
                "and web server share the same chroma_path.",
                collection, video_id, total_count, actual_path,
            )
    except Exception:
        logger.exception(
            "_verify_ingestion: verification query failed collection=%s video_id=%s chroma_path=%s",
            collection, video_id, chroma_path,
        )

