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
    include=[],  # 任务在 conf 配置完成后手动导入，确保 task_cls 生效
)

# conf 必须在任务模块导入前配置，否则 task_cls 不生效
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
    # 全局默认任务基类（指数退避 + 抖动 + 超时保护）
    task_cls="backend.tasks.base_task:BaseTask",
    # 全局超时保护（各任务可通过装饰器参数覆盖）
    task_soft_time_limit=600,   # 10 分钟软超时（抛出 SoftTimeLimitExceeded）
    task_time_limit=900,        # 15 分钟硬超时（强制终止）
)

# Celery Beat 周期调度
celery_app.conf.beat_schedule = {
    "scan-and-recover-stuck-videos": {
        "task": "backend.tasks.recovery_tasks.async_scan_and_recover_stuck_videos",
        "schedule": 300.0,  # 每 5 分钟执行一次
        "options": {"queue": "celery"},
    },
}

# ── 在 conf 配置完成后导入任务模块（task_cls 必须在此前设置） ──
import backend.tasks.transcribe_tasks  # noqa: E402, F401
import backend.tasks.extract_keyframes_tasks  # noqa: E402, F401
import backend.tasks.video_summary_tasks  # noqa: E402, F401
import backend.tasks.vector_tasks  # noqa: E402, F401
import backend.tasks.global_retrieval_tasks  # noqa: E402, F401
import backend.tasks.video_cleanup_tasks  # noqa: E402, F401
import backend.tasks.upload_finalize_tasks  # noqa: E402, F401
import backend.tasks.workflow_runtime_tasks  # noqa: E402, F401
import backend.tasks.recovery_tasks  # noqa: E402, F401
import backend.services.domain_event_listener  # noqa: E402, F401

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
