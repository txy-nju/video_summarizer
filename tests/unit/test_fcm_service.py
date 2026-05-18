"""FCM 推送服务单元测试（Mock Firebase Admin SDK）。

覆盖：
- FCMService.send() 无设备时返回 skipped
- FCMService.send() Firebase 不可用时返回 skipped
- FCMService.send_broadcast() 无设备时返回 skipped
- FCMPushRequest payload 正确传递
"""

from __future__ import annotations

import pytest

from backend.schemas.device import FCMPushData, FCMPushRequest


class TestFCMPushRequest:
    def test_push_request_construction(self) -> None:
        data = FCMPushData(
            scope="video_summary_task",
            scope_id="task_001",
            deep_link="app://tasks/task_001",
        )
        req = FCMPushRequest(
            user_id="usr_001",
            title="分析完成",
            body="您的视频总结已生成",
            data=data,
        )
        assert req.user_id == "usr_001"
        assert req.title == "分析完成"
        assert req.body == "您的视频总结已生成"
        assert req.data.scope == "video_summary_task"
        assert req.data.scope_id == "task_001"
        assert req.data.deep_link == "app://tasks/task_001"


class TestFCMServiceSendSkipped:
    """验证 FCMService.send() 在无设备或无 Firebase 时静默跳过。"""

    def test_send_skipped_when_no_device_tokens(self) -> None:
        from backend.notifications.fcm_service import FCMService

        service = FCMService()
        # Patch _get_user_device_tokens to return empty list
        original = FCMService._get_user_device_tokens

        def _empty(_self, user_id: str) -> list[str]:
            return []

        FCMService._get_user_device_tokens = _empty  # type: ignore
        try:
            result = service.send(
                FCMPushRequest(
                    user_id="usr_none",
                    title="Test",
                    body="Test body",
                    data=FCMPushData(scope="test", scope_id="id"),
                )
            )
            assert result["status"] == "skipped"
            assert result["reason"] == "no_device_tokens"
        finally:
            FCMService._get_user_device_tokens = original  # type: ignore

    def test_send_skipped_when_firebase_unavailable(self) -> None:
        import backend.notifications.fcm_service as fcm_mod

        # Make _get_firebase_messaging return None
        original_getter = fcm_mod._get_firebase_messaging
        fcm_mod._get_firebase_messaging = lambda: None  # type: ignore

        from backend.notifications.fcm_service import FCMService

        service = FCMService()
        original_tokens = FCMService._get_user_device_tokens

        def _has_tokens(_self, user_id: str) -> list[str]:
            return ["fake_token"]

        FCMService._get_user_device_tokens = _has_tokens  # type: ignore
        try:
            result = service.send(
                FCMPushRequest(
                    user_id="usr_001",
                    title="Test",
                    body="Test body",
                    data=FCMPushData(scope="test", scope_id="id"),
                )
            )
            assert result["status"] == "skipped"
            assert result["reason"] == "firebase_unavailable"
        finally:
            FCMService._get_user_device_tokens = original_tokens  # type: ignore
            fcm_mod._get_firebase_messaging = original_getter  # type: ignore

    def test_send_broadcast_skipped_when_no_devices(self) -> None:
        from backend.notifications.fcm_service import FCMService

        service = FCMService()
        original = FCMService._get_all_device_tokens

        def _empty(_self) -> list[str]:
            return []

        FCMService._get_all_device_tokens = _empty  # type: ignore
        try:
            result = service.send_broadcast("Title", "Body")
            assert result["status"] == "skipped"
            assert result["reason"] == "no_devices"
        finally:
            FCMService._get_all_device_tokens = original  # type: ignore


class TestFCMServiceBuildMessage:
    """验证 _build_message_kwargs 构造正确的 Firebase Message payload。"""

    def test_build_message_with_data(self) -> None:
        from backend.notifications.fcm_service import FCMService

        kwargs = FCMService._build_message_kwargs(
            token="test_fcm_token",
            title="分析完成",
            body="您的视频总结已生成",
            data=FCMPushData(
                scope="video_summary_task",
                scope_id="task_001",
                deep_link="app://tasks/task_001",
            ),
        )
        assert kwargs["token"] == "test_fcm_token"
        assert kwargs["notification"]["title"] == "分析完成"
        assert kwargs["notification"]["body"] == "您的视频总结已生成"
        assert kwargs["data"]["scope"] == "video_summary_task"
        assert kwargs["data"]["scope_id"] == "task_001"
        assert kwargs["data"]["deep_link"] == "app://tasks/task_001"

    def test_build_message_without_data(self) -> None:
        from backend.notifications.fcm_service import FCMService

        kwargs = FCMService._build_message_kwargs(
            token="test_fcm_token",
            title="通知",
            body="内容",
            data=None,
        )
        assert kwargs["token"] == "test_fcm_token"
        assert kwargs["notification"]["title"] == "通知"
        assert "data" not in kwargs
