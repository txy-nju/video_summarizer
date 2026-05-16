"""
全局检索域异步任务：知识库向量集合重建等后台作业。
当前为占位实现；向量库集成在步骤 7（知识检索域）完成后填充。
"""

from __future__ import annotations

import logging

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="backend.tasks.global_retrieval_tasks.async_rebuild_vector_collection",
    acks_late=True,
)
def async_rebuild_vector_collection(kbid: str) -> dict:
    """
    重建知识库向量集合（知识库删除/重建时触发）。
    占位实现：向量库集成完成前跳过执行，仅记录日志。
    """
    # TODO: 步骤 7 实现：Drop + Recreate vector collection for kbid
    logger.info(
        "async_rebuild_vector_collection: kbid=%s (placeholder, vector integration pending)",
        kbid,
    )
    return {"kbid": kbid, "status": "SKIPPED", "message": "向量集合重建待步骤 7 实现"}
