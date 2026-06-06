"""
视频资源跨存储级联清理任务。

async_cascade_delete_video 负责：
1. OSS 原始视频与关键帧目录清理（step 5.5 后接入真实 OSS client）
2. 向量库 transcript_vector_ids 对应切片删除（步骤 7 后接入）
3. 数据库物理删除

状态流转（幂等）：PENDING_DELETE -> DELETING -> PURGED / DELETE_FAILED
"""

from __future__ import annotations

import logging

from backend.infrastructure.storage.oss_client import get_object_storage_client
from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _cleanup_video_from_kb_vectors(video_id: str, linked_kbids: list[str]) -> None:
    """从各知识库的 Chroma/BM25 中清理指定视频的残留 chunk。

    幂等：重复调用不产生额外副作用。
    """
    try:
        from backend.db.session import SessionLocal
        from backend.repositories.kb_repository import KnowledgeBaseRepository
        from backend.tasks.global_retrieval_tasks import (
            _delete_video_chunks_from_collection,
            _remove_bm25_entries_by_prefix,
        )

        db = SessionLocal()
        try:
            kb_repo = KnowledgeBaseRepository(db_session=db)
            for kbid in linked_kbids:
                try:
                    kb = kb_repo.get_by_id_system(kbid)
                    if kb is None:
                        continue
                    collection_name = kb.vector_collection_name or f"kb_{kbid}"
                    chroma_del = _delete_video_chunks_from_collection(
                        collection_name, video_id, kbid=kbid
                    )
                    bm25_del = _remove_bm25_entries_by_prefix(
                        f"transcript://kb/{collection_name}/{video_id}", kbid=kbid
                    )
                    logger.info(
                        "_cleanup_video_from_kb_vectors: kbid=%s collection=%s "
                        "video_id=%s chroma_deleted=%d bm25_removed=%d",
                        kbid, collection_name, video_id, chroma_del, bm25_del,
                    )
                except Exception as exc:
                    logger.warning(
                        "_cleanup_video_from_kb_vectors: failed for kbid=%s video_id=%s: %s",
                        kbid, video_id, exc,
                    )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "_cleanup_video_from_kb_vectors: init error for video_id=%s: %s",
            video_id, exc,
        )


def _cleanup_per_video_vectors(video_id: str) -> None:
    """删除 per-video Chroma physical collection + BM25 索引目录。

    Shared by async_cascade_delete_video and async_garbage_collect_video.
    幂等：重复调用不产生额外副作用。
    """
    try:
        from pathlib import Path
        from backend.infrastructure.rag_settings_factory import build_rag_settings, _BM25_INDEX_DIR
        from modular_rag.libs.vector_store.chroma_store import ChromaStore

        collection = f"video_{video_id}"
        bm25_dir = str(Path(_BM25_INDEX_DIR) / f"video_{collection}")
        settings = build_rag_settings(collection=collection, bm25_index_dir=bm25_dir)

        # 删除 Chroma physical collection
        store = ChromaStore.from_settings(settings.vector_store)
        try:
            store._client.delete_collection(name=collection)
            logger.info(
                "_cleanup_per_video_vectors: deleted Chroma collection=%s for video_id=%s",
                collection, video_id,
            )
        except Exception as exc:
            logger.warning(
                "_cleanup_per_video_vectors: failed to delete Chroma collection=%s for video_id=%s: %s",
                collection, video_id, exc,
            )

        # 删除 BM25 索引目录
        import shutil
        bm25_dir_path = Path(bm25_dir)
        if bm25_dir_path.exists():
            shutil.rmtree(bm25_dir_path)
            logger.info(
                "_cleanup_per_video_vectors: deleted BM25 index dir=%s for video_id=%s",
                bm25_dir, video_id,
            )
    except Exception as exc:
        logger.warning(
            "_cleanup_per_video_vectors: error for video_id=%s: %s",
            video_id, exc,
        )


def _create_video_resource_service():
    """工厂：为每次任务调用创建独立 DB session + service 实例。"""
    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    return VideoResourceService(VideoResourceRepository(db)), db


@celery_app.task(
    bind=True,
    name="backend.tasks.video_cleanup_tasks.async_cascade_delete_video",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    task_soft_time_limit=300,
    task_time_limit=600,
)
def async_cascade_delete_video(self, video_id: str, linked_kbids: list[str] | None = None) -> dict:
    """
    异步级联清理视频跨存储资源，推进删除状态机。
    幂等：重复调用不产生额外副作用。
    仅由软删除接口触发（通过 VideoResourceService），禁止在请求线程内直接调用。

    linked_kbids: 视频删除前关联的知识库 ID 列表，用于清理 per-KB Chroma/BM25 中的残留 chunk。
    """
    service, db = _create_video_resource_service()
    try:
        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            # 已物理删除或从未存在，视为成功
            logger.info("async_cascade_delete_video: video_id=%s already gone", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}

        # 推进状态：PENDING_DELETE -> DELETING
        service.mark_deletion_in_progress(video_id=video_id)

        # 1. OSS 清理
        storage_client = get_object_storage_client()
        if video.oss_key:
            storage_client.delete_object(video.oss_key)
        keyframes_oss_prefix = getattr(video, "keyframes_oss_prefix", None)
        if keyframes_oss_prefix:
            storage_client.delete_prefix(keyframes_oss_prefix)

        # 2. Per-KB 向量清理：从各知识库的 Chroma/BM25 中删除该视频的残留 chunk
        if linked_kbids:
            _cleanup_video_from_kb_vectors(video_id, linked_kbids)

        # 3. 向量库清理：删除 per-video Chroma physical collection + BM25 索引目录
        _cleanup_per_video_vectors(video_id)

        # 4. 数据库物理删除
        service.purge_video(video_id=video_id)

        logger.info("async_cascade_delete_video: video_id=%s purged", video_id)
        return {"video_id": video_id, "deletion_status": "PURGED"}

    except Exception as exc:
        logger.exception("async_cascade_delete_video failed for video_id=%s", video_id)
        # 仅在重试耗尽时标记 DELETE_FAILED；重试期间保留原状态
        if self.is_last_attempt:
            logger.critical(
                "async_cascade_delete_video: retries exhausted for video_id=%s", video_id,
            )
            try:
                fail_service, fail_db = _create_video_resource_service()
                fail_service.mark_deletion_failed(video_id=video_id)
                fail_db.close()
            except Exception:
                pass
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="backend.tasks.video_cleanup_tasks.async_garbage_collect_video",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    task_soft_time_limit=300,
    task_time_limit=600,
)
def async_garbage_collect_video(self, video_id: str, linked_kbids: list[str] | None = None) -> dict:
    """
    Garbage-collect a video when its task_ref_count drops to zero.

    Triggered automatically by:
    - VideoSummaryTaskService.delete_video_summary_task (single task deletion)
    - KnowledgeBaseService.delete_knowledge_base (KB cascade deletion)

    Guards:
    - Re-reads ref_count; aborts if > 0 (race: new task created during dispatch delay).
    - Checks deletion_status; aborts if already in manual deletion flow.

    幂等：重复调用不产生额外副作用。
    """
    service, db = _create_video_resource_service()
    try:
        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            logger.info("async_garbage_collect_video: video_id=%s already gone", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}

        # Guard 1: ref_count must be zero
        ref_count = video.task_ref_count or 0
        if ref_count > 0:
            logger.info(
                "async_garbage_collect_video: video_id=%s ref_count=%d > 0, aborting",
                video_id, ref_count,
            )
            return {"video_id": video_id, "status": "ABORTED_REF_COUNT_NONZERO", "ref_count": ref_count}

        # Guard 2: don't interfere with manual deletion flow
        deletion_status = getattr(video, "deletion_status", "NONE") or "NONE"
        if deletion_status not in ("NONE", ""):
            logger.info(
                "async_garbage_collect_video: video_id=%s already in deletion flow (%s), aborting",
                video_id, deletion_status,
            )
            return {"video_id": video_id, "status": "ABORTED_DELETION_IN_PROGRESS"}

        # Mark as DELETING
        service.mark_deletion_in_progress(video_id=video_id)

        # 1. Local storage cleanup
        storage_client = get_object_storage_client()
        if video.oss_key:
            storage_client.delete_object(video.oss_key)
        keyframes_oss_prefix = getattr(video, "keyframes_oss_prefix", None)
        if keyframes_oss_prefix:
            storage_client.delete_prefix(keyframes_oss_prefix)

        # 2. Per-KB vector cleanup
        kbids = linked_kbids or []
        if kbids:
            _cleanup_video_from_kb_vectors(video_id, kbids)

        # 3. Per-video Chroma + BM25 cleanup
        _cleanup_per_video_vectors(video_id)

        # 4. Database physical delete
        service.purge_video(video_id=video_id)

        logger.info("async_garbage_collect_video: video_id=%s garbage collected", video_id)
        return {"video_id": video_id, "status": "COLLECTED"}

    except Exception as exc:
        logger.exception("async_garbage_collect_video failed for video_id=%s", video_id)
        if self.is_last_attempt:
            logger.critical(
                "async_garbage_collect_video: retries exhausted for video_id=%s", video_id,
            )
            try:
                fail_service, fail_db = _create_video_resource_service()
                fail_service.mark_deletion_failed(video_id=video_id)
                fail_db.close()
            except Exception:
                pass
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()
