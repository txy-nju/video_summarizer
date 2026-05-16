"""
Celery 应用实例。
所有任务模块必须通过此实例注册，不得自行创建独立的 Celery 实例。
Broker 与 Result Backend 均由 backend/config.py 的 Settings 注入，
通过环境变量 CELERY_BROKER_URL / CELERY_RESULT_BACKEND 覆盖默认值。
"""

from celery import Celery
from backend.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "video_summarizer",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=[
        "backend.tasks.transcribe_tasks",
        "backend.tasks.extract_keyframes_tasks",
        "backend.tasks.video_summary_tasks",
        "backend.tasks.vector_tasks",
        "backend.tasks.global_retrieval_tasks",
        "backend.tasks.video_cleanup_tasks",
        "backend.tasks.upload_finalize_tasks",
        "backend.services.domain_event_listener",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # 任务至少执行完成后才确认，避免 worker 崩溃导致任务丢失
    task_acks_late=True,
    # worker 意外中断时将未确认任务重新入队
    task_reject_on_worker_lost=True,
    # 结果保留时长：24 小时
    result_expires=86400,
)
