"""Device Schema 验证测试。

覆盖：
- DeviceRegisterRequest 验证（有效/无效 payload）
- FCMPushPayload 序列化格式对齐计划约定
- Platform 枚举值完整性
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceUnregisterRequest,
    FCMPushData,
    FCMPushPayload,
    FCMPushRequest,
    Platform,
)


class TestPlatformEnum:
    def test_platform_has_android_ios_web(self) -> None:
        assert Platform.ANDROID == "android"
        assert Platform.IOS == "ios"
        assert Platform.WEB == "web"

    def test_platform_from_string(self) -> None:
        assert Platform("android") == Platform.ANDROID
        assert Platform("ios") == Platform.IOS

    def test_platform_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Platform("windows")


class TestDeviceRegisterRequest:
    def test_valid_minimal_payload(self) -> None:
        req = DeviceRegisterRequest(
            device_token="fcm_token_xxx",
            platform="android",
            device_id="android_001",
        )
        assert req.device_token == "fcm_token_xxx"
        assert req.platform == Platform.ANDROID
        assert req.app_version == ""
        assert req.device_id == "android_001"

    def test_valid_full_payload(self) -> None:
        req = DeviceRegisterRequest(
            device_token="fcm_token_yyy",
            platform="ios",
            app_version="2.1.0",
            device_id="iphone_xyz",
        )
        assert req.app_version == "2.1.0"
        assert req.platform == Platform.IOS

    def test_missing_device_token_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(platform="android", device_id="d1")

    def test_missing_platform_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(device_token="t1", device_id="d1")

    def test_missing_device_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(device_token="t1", platform="android")

    def test_empty_device_token_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(device_token="", platform="android", device_id="d1")

    def test_device_token_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(
                device_token="x" * 513,
                platform="android",
                device_id="d1",
            )

    def test_device_id_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(
                device_token="t1",
                platform="android",
                device_id="x" * 129,
            )

    def test_app_version_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            DeviceRegisterRequest(
                device_token="t1",
                platform="android",
                device_id="d1",
                app_version="x" * 33,
            )


class TestFCMPushData:
    def test_valid_push_data(self) -> None:
        data = FCMPushData(
            scope="video_summary_task",
            scope_id="task_001",
            deep_link="app://tasks/task_001",
        )
        assert data.scope == "video_summary_task"
        assert data.scope_id == "task_001"
        assert data.deep_link == "app://tasks/task_001"

    def test_push_data_empty_deep_link(self) -> None:
        data = FCMPushData(scope="video_summary_task", scope_id="task_001")
        assert data.deep_link == ""

    def test_missing_scope_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushData(scope_id="task_001")

    def test_missing_scope_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushData(scope="video_summary_task")


class TestFCMPushPayload:
    def test_push_payload_plan_format(self) -> None:
        """验证推送载荷对齐计划约定的 JSON 格式。"""
        payload = FCMPushPayload(
            title="分析完成",
            body="您的视频总结已生成",
            data=FCMPushData(
                scope="video_summary_task",
                scope_id="task_001",
                deep_link="app://tasks/task_001",
            ),
        )
        json_data = payload.model_dump()
        assert json_data["title"] == "分析完成"
        assert json_data["body"] == "您的视频总结已生成"
        assert json_data["data"]["scope"] == "video_summary_task"
        assert json_data["data"]["scope_id"] == "task_001"
        assert json_data["data"]["deep_link"] == "app://tasks/task_001"

    def test_push_payload_missing_title_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushPayload(
                body="body",
                data=FCMPushData(scope="s", scope_id="id"),
            )

    def test_push_payload_empty_title_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushPayload(
                title="",
                body="body",
                data=FCMPushData(scope="s", scope_id="id"),
            )

    def test_push_payload_missing_body_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushPayload(
                title="title",
                data=FCMPushData(scope="s", scope_id="id"),
            )


class TestFCMPushRequest:
    def test_push_request_valid(self) -> None:
        req = FCMPushRequest(
            user_id="usr_001",
            title="任务完成",
            body="您的总结已生成",
            data=FCMPushData(scope="video_summary_task", scope_id="task_001"),
        )
        assert req.user_id == "usr_001"
        assert req.title == "任务完成"

    def test_push_request_missing_user_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            FCMPushRequest(
                title="t",
                body="b",
                data=FCMPushData(scope="s", scope_id="id"),
            )


class TestDeviceRegisterResponse:
    def test_register_response_format(self) -> None:
        resp = DeviceRegisterResponse(
            device_token_id="dt_001",
            platform=Platform.ANDROID,
            device_id="android_001",
        )
        assert resp.device_token_id == "dt_001"
        assert resp.platform == Platform.ANDROID
        assert resp.device_id == "android_001"
        assert resp.registered_at  # auto-generated ISO timestamp


class TestDeviceUnregisterRequest:
    def test_unregister_by_token(self) -> None:
        req = DeviceUnregisterRequest(device_token="fcm_token_xxx")
        assert req.device_token == "fcm_token_xxx"
        assert req.device_id is None

    def test_unregister_by_device_id(self) -> None:
        req = DeviceUnregisterRequest(device_id="android_001")
        assert req.device_id == "android_001"
        assert req.device_token is None

    def test_unregister_empty_both(self) -> None:
        req = DeviceUnregisterRequest()
        assert req.device_token is None
        assert req.device_id is None
