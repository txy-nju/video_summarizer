"""
全局检索域异步任务：知识库向量集合重建与清理。
"""

from __future__ import annotations

import logging

from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="backend.tasks.global_retrieval_tasks.async_rebuild_vector_collection",
    acks_late=True,
    max_retries=2,
    default_retry_delay=60,
    task_soft_time_limit=1800,
    task_time_limit=3600,
)
def async_rebuild_vector_collection(self, kbid: str) -> dict:
    """
    全量重建知识库向量集合。
    1. 查询 KB 的 vector_collection_name 与所有关联视频
    2. 删除 Chroma + BM25 中该 collection 的所有数据
    3. 重新摄取各视频的转录文本
    source_path 使用 transcript://kb/{collection}/{video_id}，
    与单视频 collection（transcript://video/{video_id}）的 source_path 不同，
    避免 Chroma chunk_id 冲突。
    先删后建保证幂等性。
    """
    from backend.db.session import SessionLocal
    from backend.repositories.kb_repository import KnowledgeBaseRepository
    from backend.repositories.video_resource_repository import VideoResourceRepository

    db = SessionLocal()
    try:
        kb_repo = KnowledgeBaseRepository(db_session=db)
        kb = kb_repo.get_by_id_system(kbid)
        if kb is None:
            logger.warning("async_rebuild_vector_collection: kbid=%s not found in DB", kbid)
            return {"kbid": kbid, "status": "NOT_FOUND"}

        collection_name = kb.vector_collection_name or f"kb_{kbid}"
        video_ids = kb_repo.get_linked_video_ids_system(kbid)

        if not video_ids:
            logger.info(
                "async_rebuild_vector_collection: kbid=%s collection=%s no linked videos, purging only",
                kbid, collection_name,
            )
            _purge_chroma_collection(collection_name)
            _remove_bm25_entries_by_prefix(f"transcript://kb/{collection_name}/")
            return {"kbid": kbid, "collection": collection_name, "status": "PURGED", "videos_ingested": 0}

        # 清空旧 collection 数据（先删后建，保证幂等）
        _purge_chroma_collection(collection_name)
        _remove_bm25_entries_by_prefix(f"transcript://kb/{collection_name}/")

        video_repo = VideoResourceRepository(db_session=db)
        ingested = 0
        skipped = 0
        for vid in video_ids:
            video = video_repo.get_by_id_system(vid)
            if video is None:
                continue
            transcript = getattr(video, "full_transcript", None) or ""
            if not transcript.strip():
                skipped += 1
                continue
            base_metadata = {
                "source_path": f"transcript://kb/{collection_name}/{vid}",
                "video_id": vid,
                "owner_id": str(getattr(video, "owner_id", "")),
                "doc_type": "transcript",
            }
            segments = getattr(video, "transcript_segments", None) or []
            from backend.tasks.vector_tasks import _run_rag_ingestion_with_segments, _run_rag_ingestion
            if segments:
                _run_rag_ingestion_with_segments(
                    segments=segments,
                    collection=collection_name,
                    base_metadata=base_metadata,
                )
            else:
                _run_rag_ingestion(
                    text=transcript,
                    collection=collection_name,
                    metadata=base_metadata,
                )
            ingested += 1

        logger.info(
            "async_rebuild_vector_collection: kbid=%s collection=%s ingested=%d skipped=%d",
            kbid, collection_name, ingested, skipped,
        )

        # 重建完成后验证
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        _settings = build_rag_settings()
        _chroma_path = getattr(_settings.vector_store, "persist_path", "N/A")
        _verify_kb_collection(collection_name, _chroma_path)

        return {
            "kbid": kbid,
            "collection": collection_name,
            "status": "COMPLETED",
            "videos_ingested": ingested,
            "videos_skipped": skipped,
        }
    except Exception as exc:
        logger.exception("async_rebuild_vector_collection failed for kbid=%s", kbid)
        if self.is_last_attempt:
            logger.critical("async_rebuild_vector_collection: retries exhausted for kbid=%s", kbid)
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="backend.tasks.global_retrieval_tasks.async_add_video_to_vector_collection",
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    task_soft_time_limit=600,
    task_time_limit=900,
)
def async_add_video_to_vector_collection(self, kbid: str, video_id: str) -> dict:
    """
    增量摄取：将单个视频的转录文本追加到知识库向量集合。
    - source_path 使用 transcript://kb/{collection}/{video_id}，
      与单视频 collection 的 source_path（transcript://video/{video_id}）不同，
      保证 Chroma chunk_id 唯一，避免互相覆盖。
    - Chroma upsert 天然幂等，重复调用不会产生重复数据。
    - Embedding 调用量 = O(1 个视频)。
    """
    from backend.db.session import SessionLocal
    from backend.repositories.kb_repository import KnowledgeBaseRepository
    from backend.repositories.video_resource_repository import VideoResourceRepository

    db = SessionLocal()
    try:
        kb_repo = KnowledgeBaseRepository(db_session=db)
        kb = kb_repo.get_by_id_system(kbid)
        if kb is None:
            logger.warning("async_add_video_to_vector_collection: kbid=%s not found", kbid)
            return {"kbid": kbid, "video_id": video_id, "status": "KB_NOT_FOUND"}

        collection_name = kb.vector_collection_name or f"kb_{kbid}"
        video_repo = VideoResourceRepository(db_session=db)
        video = video_repo.get_by_id_system(video_id)
        if video is None:
            return {"kbid": kbid, "video_id": video_id, "status": "VIDEO_NOT_FOUND"}

        transcript = getattr(video, "full_transcript", None) or ""
        if not transcript.strip():
            logger.info(
                "async_add_video_to_vector_collection: kbid=%s video_id=%s empty transcript, skip",
                kbid, video_id,
            )
            return {"kbid": kbid, "video_id": video_id, "status": "SKIPPED", "reason": "empty transcript"}

        if _is_video_indexed_in_collection(collection_name, video_id):
            logger.info(
                "async_add_video_to_vector_collection: kbid=%s collection=%s video_id=%s already indexed, skip",
                kbid, collection_name, video_id,
            )
            return {"kbid": kbid, "collection": collection_name, "video_id": video_id, "status": "ALREADY_INDEXED"}

        base_metadata = {
            "source_path": f"transcript://kb/{collection_name}/{video_id}",
            "video_id": video_id,
            "owner_id": str(getattr(video, "owner_id", "")),
            "doc_type": "transcript",
        }
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        _settings = build_rag_settings()
        _chroma_path = getattr(_settings.vector_store, "persist_path", "N/A")

        segments = getattr(video, "transcript_segments", None) or []
        from backend.tasks.vector_tasks import _run_rag_ingestion_with_segments, _run_rag_ingestion
        if segments:
            _run_rag_ingestion_with_segments(
                segments=segments,
                collection=collection_name,
                base_metadata=base_metadata,
            )
        else:
            _run_rag_ingestion(
                text=transcript,
                collection=collection_name,
                metadata=base_metadata,
            )

        # 摄取后立即验证
        _verify_kb_ingestion(collection_name, video_id, _chroma_path)

        logger.info(
            "async_add_video_to_vector_collection: kbid=%s collection=%s video_id=%s "
            "ingested chroma_path=%s",
            kbid, collection_name, video_id, _chroma_path,
        )
        return {"kbid": kbid, "collection": collection_name, "video_id": video_id, "status": "COMPLETED"}
    except Exception as exc:
        logger.exception(
            "async_add_video_to_vector_collection failed for kbid=%s video_id=%s", kbid, video_id
        )
        if self.is_last_attempt:
            logger.critical(
                "async_add_video_to_vector_collection: retries exhausted for kbid=%s video_id=%s",
                kbid, video_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="backend.tasks.global_retrieval_tasks.async_remove_video_from_vector_collection",
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    task_soft_time_limit=300,
    task_time_limit=600,
)
def async_remove_video_from_vector_collection(self, kbid: str, video_id: str) -> dict:
    """
    增量删除：从知识库向量集合中精准删除指定视频的所有 chunk。
    - Chroma：双字段过滤 {collection, video_id}
    - BM25：按 source_path 前缀精准移除，重算 IDF 后保存
    无需重新摄取任何视频，Embedding 调用量 = 0。
    删除操作天然幂等，重复调用安全。
    """
    from backend.db.session import SessionLocal
    from backend.repositories.kb_repository import KnowledgeBaseRepository

    db = SessionLocal()
    try:
        kb_repo = KnowledgeBaseRepository(db_session=db)
        kb = kb_repo.get_by_id_system(kbid)
        collection_name = (kb.vector_collection_name or f"kb_{kbid}") if kb else f"kb_{kbid}"
    finally:
        db.close()

    try:
        chroma_deleted = _delete_video_chunks_from_collection(collection_name, video_id)
        bm25_removed = _remove_bm25_entries_by_prefix(
            f"transcript://kb/{collection_name}/{video_id}"
        )
        logger.info(
            "async_remove_video_from_vector_collection: kbid=%s collection=%s video_id=%s "
            "chroma_deleted=%d bm25_removed=%d",
            kbid, collection_name, video_id, chroma_deleted, bm25_removed,
        )
        return {
            "kbid": kbid, "collection": collection_name, "video_id": video_id,
            "status": "REMOVED", "chroma_deleted": chroma_deleted, "bm25_removed": bm25_removed,
        }
    except Exception as exc:
        logger.exception(
            "async_remove_video_from_vector_collection failed for kbid=%s video_id=%s",
            kbid, video_id,
        )
        if self.is_last_attempt:
            logger.critical(
                "async_remove_video_from_vector_collection: retries exhausted for kbid=%s video_id=%s",
                kbid, video_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())


@celery_app.task(
    bind=True,
    name="backend.tasks.global_retrieval_tasks.async_purge_vector_collection",
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
    task_soft_time_limit=300,
    task_time_limit=600,
)
def async_purge_vector_collection(self, collection_name: str) -> dict:
    """
    清理向量集合中属于 collection_name 的所有 chunk（KB 删除时调用）。
    collection_name 由调用方（kb_service）在 DB 删除前传入。
    同步清理 Chroma + BM25。
    删除操作天然幂等，重复调用安全。
    """
    try:
        chroma_deleted = _purge_chroma_collection(collection_name)
        bm25_removed = _remove_bm25_entries_by_prefix(f"transcript://kb/{collection_name}/")
        logger.info(
            "async_purge_vector_collection: collection=%s chroma_deleted=%d bm25_removed=%d",
            collection_name, chroma_deleted, bm25_removed,
        )
        return {"collection": collection_name, "status": "PURGED",
                "chroma_deleted": chroma_deleted, "bm25_removed": bm25_removed}
    except Exception as exc:
        logger.exception(
            "async_purge_vector_collection failed for collection=%s", collection_name,
        )
        if self.is_last_attempt:
            logger.critical(
                "async_purge_vector_collection: retries exhausted for collection=%s", collection_name,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())


# ── 工具函数 ────────────────────────────────────────────────────────────────

def _purge_chroma_collection(collection_name: str) -> int:
    """用 delete_by_metadata 删除 Chroma 中所有 collection == collection_name 的向量记录。"""
    try:
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        settings = build_rag_settings()
        store = ChromaStore.from_settings(settings.vector_store)
        return store.delete_by_metadata({"collection": collection_name})
    except Exception:
        logger.exception("_purge_chroma_collection: failed for collection=%s", collection_name)
        return 0


def _delete_video_chunks_from_collection(collection_name: str, video_id: str) -> int:
    """精准删除指定 collection 中属于 video_id 的所有 chunk（双字段过滤）。"""
    try:
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        settings = build_rag_settings()
        store = ChromaStore.from_settings(settings.vector_store)
        return store.delete_by_metadata({"collection": collection_name, "video_id": video_id})
    except Exception:
        logger.exception(
            "_delete_video_chunks_from_collection: failed collection=%s video_id=%s",
            collection_name, video_id,
        )
        return 0


def _is_video_indexed_in_collection(collection_name: str, video_id: str) -> bool:
    """检查 Chroma 中是否已存在该 KB collection 内属于 video_id 的向量数据。
    双字段过滤 {collection, video_id}，limit=1，开销极低。
    """
    try:
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        settings = build_rag_settings()
        store = ChromaStore.from_settings(settings.vector_store)
        results = store.get_by_metadata(
            {"collection": collection_name, "video_id": video_id}, limit=1
        )
        return len(results) > 0
    except Exception:
        logger.exception(
            "_is_video_indexed_in_collection: check failed collection=%s video_id=%s, will re-ingest",
            collection_name, video_id,
        )
        return False


def _verify_kb_ingestion(collection_name: str, video_id: str, chroma_path: str) -> None:
    """验证 KB 级别单视频摄取后向量数据是否已成功写入 Chroma。

    摄取完成后立即查询 Chroma，用 {collection, video_id} 双字段过滤。
    """
    try:
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        settings = build_rag_settings()
        actual_path = getattr(settings.vector_store, "persist_path", "N/A")
        store = ChromaStore.from_settings(settings.vector_store)

        results = store.get_by_metadata(
            {"collection": collection_name, "video_id": video_id}, limit=5
        )
        total_stats = store.get_collection_stats()

        if results:
            sample_meta = results[0].metadata or {}
            logger.info(
                "_verify_kb_ingestion: OK collection=%s video_id=%s "
                "found_chunks=%d total_chroma_chunks=%s chroma_path=%s "
                "sample_chunk_id=%s sample_meta_keys=%s",
                collection_name, video_id, len(results),
                total_stats.get("chunk_count", -1), actual_path,
                results[0].id, list(sample_meta.keys()),
            )
        else:
            logger.error(
                "_verify_kb_ingestion: FAILED collection=%s video_id=%s "
                "found_chunks=0 total_chroma_chunks=%s chroma_path=%s "
                "→ Vectors may NOT be persisted! Check Celery worker vs "
                "web server chroma_path consistency.",
                collection_name, video_id,
                total_stats.get("chunk_count", -1), actual_path,
            )
    except Exception:
        logger.exception(
            "_verify_kb_ingestion: verification query failed collection=%s video_id=%s "
            "chroma_path=%s",
            collection_name, video_id, chroma_path,
        )


def _verify_kb_collection(collection_name: str, chroma_path: str) -> None:
    """验证 KB 级别 collection 中是否有向量数据。

    用 collection 单字段过滤，确认至少有 1 条记录。
    """
    try:
        from modular_rag.libs.vector_store.chroma_store import ChromaStore
        from backend.infrastructure.rag_settings_factory import build_rag_settings
        settings = build_rag_settings()
        actual_path = getattr(settings.vector_store, "persist_path", "N/A")
        store = ChromaStore.from_settings(settings.vector_store)

        results = store.get_by_metadata({"collection": collection_name}, limit=5)
        total_stats = store.get_collection_stats()

        if results:
            video_ids = list({r.metadata.get("video_id", "?") for r in results})
            logger.info(
                "_verify_kb_collection: OK collection=%s found_chunks=%d "
                "total_chroma_chunks=%s chroma_path=%s video_ids=%s",
                collection_name, len(results),
                total_stats.get("chunk_count", -1), actual_path, video_ids,
            )
        else:
            logger.error(
                "_verify_kb_collection: FAILED collection=%s found_chunks=0 "
                "total_chroma_chunks=%s chroma_path=%s → Collection may be empty!",
                collection_name, total_stats.get("chunk_count", -1), actual_path,
            )
    except Exception:
        logger.exception(
            "_verify_kb_collection: verification query failed collection=%s chroma_path=%s",
            collection_name, chroma_path,
        )


def _remove_bm25_entries_by_prefix(source_path_prefix: str) -> int:
    """
    从全局 BM25 索引中删除 source_path 包含指定前缀的所有文档条目，
    随后重算 IDF 并保存。不依赖 embedding，调用代价极低。

    原理：BM25Indexer._documents 是以 chunk_id 为 key 的字典，
    每条目记录 {source_path, terms, doc_length}。直接过滤再重建即可。
    """
    try:
        from modular_rag.ingestion.storage.bm25_indexer import BM25Indexer
        indexer = BM25Indexer()
        if not indexer.index_path.exists():
            return 0
        indexer.load()
        before = len(indexer._documents)
        indexer._documents = {
            k: v for k, v in indexer._documents.items()
            if source_path_prefix not in v.get("source_path", "")
        }
        removed = before - len(indexer._documents)
        if removed:
            # rebuild=False + 空 records → 只重算 IDF 并落盘，不新增任何条目
            indexer.build([], rebuild=False)
        return removed
    except Exception:
        logger.exception("_remove_bm25_entries_by_prefix: failed prefix=%s", source_path_prefix)
        return 0

