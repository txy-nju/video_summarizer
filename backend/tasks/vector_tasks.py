"""
向量化任务：对转录文本分块并写入向量库（低优先级后台队列）。
当前为占位实现；向量库集成在步骤 7（知识检索域）完成后填充。
"""

from __future__ import annotations

import logging

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="backend.tasks.vector_tasks.async_embed_transcript_chunks_background",
    acks_late=True,
    queue="low_priority",
)
def async_embed_transcript_chunks_background(video_id: str) -> dict:
    """
    后台向量化任务：将 full_transcript 分块嵌入向量库，填充 transcript_vector_ids。
    运行在低优先级队列，不阻塞视频就绪主流程。
    占位实现：向量库集成完成前跳过执行，仅记录日志。
    """
    # TODO: 步骤 7 实现向量化逻辑：
    #   1. 读取 full_transcript
    #   2. 按语义分块
    #   3. 调用嵌入模型生成向量
    #   4. 写入向量库
    #   5. 更新 transcript_vector_ids
    logger.info(
        "async_embed_transcript_chunks_background: video_id=%s (placeholder, vector integration pending)",
        video_id,
    )
    return {"video_id": video_id, "status": "SKIPPED", "message": "向量化后台任务待步骤 7 实现"}
