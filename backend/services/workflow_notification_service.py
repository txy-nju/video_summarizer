"""
WorkflowNotificationService - Unified FCM notification orchestration for workflow state transitions.

Encapsulates:
- Mapping workflow events (completed/failed/etc) to FCM payloads
- Querying user's registered devices
- Triggering FCM push notifications
- Error handling and logging

Triggered by:
- WorkflowOrchestrationService state transitions (COMPLETED, FAILED, etc)
- NOT automatic; called explicitly from service/task layers
"""

from __future__ import annotations

import logging
from typing import Any

from backend.notifications.fcm_service import FCMService
from backend.repositories.device_repository import DeviceRepository
from backend.schemas.device import FCMPushData, FCMPushPayload, FCMPushRequest

logger = logging.getLogger(__name__)


class WorkflowNotificationService:
    """FCM 推送编排服务：基于工作流状态机事件触发用户推送通知。

    职责（对齐计划）：
    - 根据工作流状态变化决定是否推送（COMPLETED/FAILED）
    - 构造符合计划格式的推送载荷
    - 查询用户已注册设备并转发 FCM 推送
    - 失败不阻塞主流程（best effort）

    约束（对齐计划 6.8）：
    - 推送时机由工作流状态机显式事件触发（如 COMPLETED/FAILED）
    - 推送服务不负责决定"何时通知"；业务层显式调用此服务
    - 单用户多设备时依次推送
    - 推送失败仅记录日志，不向上传播
    """

    def __init__(
        self,
        fcm_service: FCMService,
        device_repository: DeviceRepository,
    ):
        """Initialize notification service with FCM and device persistence.

        Args:
            fcm_service: FCM 推送实现
            device_repository: 设备令牌持久化仓储
        """
        self._fcm_service = fcm_service
        self._device_repository = device_repository

    # ====================================================================
    # 工作流事件推送接口
    # ====================================================================

    def notify_workflow_completed(
        self,
        user_id: str,
        task_id: str,
        task_title: str | None = None,
    ) -> dict[str, Any]:
        """Notify user when workflow completes successfully.

        Args:
            user_id: User to notify
            task_id: Completed task ID
            task_title: Optional task title for display

        Returns:
            Dict with push results:
            - device_count: Number of devices notified
            - success_count: Number of successful pushes
            - failed_count: Number of failed pushes
        """
        logger.info(f"[WorkflowNotif] Notifying completion: user_id={user_id}, task_id={task_id}")

        title = "✅ 视频总结已完成"
        body = f"您的视频总结 {task_title or f'[{task_id[:8]}]'} 已生成，点击查看"

        payload = FCMPushPayload(
            title=title,
            body=body,
            data=FCMPushData(
                scope="video_summary_task",
                scope_id=task_id,
                deep_link=f"app://tasks/{task_id}",
            ),
        )

        return self._send_push_to_user(user_id=user_id, payload=payload)

    def notify_workflow_failed(
        self,
        user_id: str,
        task_id: str,
        error_message: str | None = None,
        task_title: str | None = None,
    ) -> dict[str, Any]:
        """Notify user when workflow encounters an error.

        Args:
            user_id: User to notify
            task_id: Failed task ID
            error_message: Optional error description
            task_title: Optional task title for display

        Returns:
            Dict with push results (as per notify_workflow_completed)
        """
        logger.info(f"[WorkflowNotif] Notifying failure: user_id={user_id}, task_id={task_id}, error={error_message}")

        title = "❌ 视频总结失败"
        error_detail = f": {error_message}" if error_message else ""
        body = f"您的视频总结 {task_title or f'[{task_id[:8]}]'} 处理失败{error_detail}，请重试"

        payload = FCMPushPayload(
            title=title,
            body=body,
            data=FCMPushData(
                scope="video_summary_task",
                scope_id=task_id,
                deep_link=f"app://tasks/{task_id}/error",
            ),
        )

        return self._send_push_to_user(user_id=user_id, payload=payload)

    def notify_workflow_approval_required(
        self,
        user_id: str,
        task_id: str,
        chunk_count: int = 0,
        task_title: str | None = None,
    ) -> dict[str, Any]:
        """Notify user that phase-1 analysis is complete and awaiting approval.

        Args:
            user_id: User to notify
            task_id: Task requiring approval
            chunk_count: Number of chunks processed (optional)
            task_title: Optional task title for display

        Returns:
            Dict with push results (as per notify_workflow_completed)
        """
        logger.info(f"[WorkflowNotif] Notifying approval required: user_id={user_id}, task_id={task_id}")

        title = "⏳ 分析完成，等待您的审核"
        chunk_info = f"（{chunk_count} 个分片）" if chunk_count > 0 else ""
        body = f"您的视频总结 {task_title or f'[{task_id[:8]}]'} {chunk_info} 初稿已生成，请审核并指导最终成文"

        payload = FCMPushPayload(
            title=title,
            body=body,
            data=FCMPushData(
                scope="video_summary_task",
                scope_id=task_id,
                deep_link=f"app://tasks/{task_id}/approve",
            ),
        )

        return self._send_push_to_user(user_id=user_id, payload=payload)

    # ====================================================================
    # 内部实现
    # ====================================================================

    def _send_push_to_user(
        self,
        user_id: str,
        payload: FCMPushPayload,
    ) -> dict[str, Any]:
        """Send FCM push to all registered devices of a user.

        Args:
            user_id: User to notify
            payload: FCM push payload

        Returns:
            Dict with push results:
            - device_count: Total devices for user
            - success_count: Successful pushes
            - failed_count: Failed pushes
        """
        try:
            # Query user's registered devices
            devices = self._device_repository.list_by_user(user_id=user_id)
            if not devices:
                logger.debug(f"[WorkflowNotif] No registered devices for user_id={user_id}")
                return {
                    "device_count": 0,
                    "success_count": 0,
                    "failed_count": 0,
                    "message": "No registered devices",
                }

            # Send push to each device
            success_count = 0
            failed_count = 0

            for device in devices:
                try:
                    request = FCMPushRequest(
                        user_id=user_id,
                        title=payload.title,
                        body=payload.body,
                        data=payload.data,
                    )
                    self._fcm_service.send(request=request)
                    success_count += 1
                except Exception as e:
                    logger.warning(
                        f"[WorkflowNotif] Failed to send push to device {device.device_id}: {e}",
                        exc_info=False,
                    )
                    failed_count += 1

            logger.info(
                f"[WorkflowNotif] Push sent to user_id={user_id}: {success_count} success, {failed_count} failed"
            )

            return {
                "device_count": len(devices),
                "success_count": success_count,
                "failed_count": failed_count,
                "message": f"Pushed to {success_count}/{len(devices)} devices",
            }

        except Exception as e:
            logger.error(
                f"[WorkflowNotif] Error sending push to user_id={user_id}: {e}",
                exc_info=True,
            )
            return {
                "device_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "message": f"Error: {str(e)}",
            }
