"""
Celery 应用实例。
所有任务模块必须通过此实例注册，不得自行创建独立的 Celery 实例。
Broker 与 Result Backend 均由 backend/config.py 的 Settings 注入，
通过环境变量 CELERY_BROKER_URL / CELERY_RESULT_BACKEND 覆盖默认值。
"""

from celery import Celery
from backend.config import get_settings
from backend.observability.tracing import configure_tracing
from backend.tasks.task_trace_hooks import register_task_trace_hooks

_settings = get_settings()
configure_tracing(
    enabled=_settings.otel_enabled,
    service_name=_settings.otel_service_name,
    exporter=_settings.otel_exporter,
    sample_ratio=_settings.otel_sample_ratio,
    jaeger_endpoint=_settings.otel_jaeger_endpoint,
    otlp_endpoint=_settings.otel_otlp_endpoint,
)

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
        "backend.tasks.workflow_runtime_tasks",
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

register_task_trace_hooks()


# ---------------------------------------------------------------------------
# Domain event listener auto-start (daemon thread on worker ready)
# ---------------------------------------------------------------------------
from celery.signals import worker_ready


@worker_ready.connect
def _start_domain_event_listener(**kwargs: object) -> None:
    """当 Celery worker 就绪时，在独立守护线程中启动域事件监听器。

    该监听器阻塞式消费 Redis Streams 中的 VideoUploadedEvent，
    并在收到事件后触发 async_process_video（转录 + 抽帧并行）。
    """
    import threading

    from backend.services.domain_event_listener import run_domain_event_listener

    thread = threading.Thread(target=run_domain_event_listener, daemon=True, name="domain-event-listener")
    thread.start()

    import logging

    _logger = logging.getLogger(__name__)
    _logger.info("Domain event listener started in daemon thread (worker_ready signal).")
