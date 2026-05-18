"""设备注册路由 API 集成测试。

覆盖：
- POST /api/v1/devices：注册 FCM token
- GET /api/v1/devices：列出用户设备
- DELETE /api/v1/devices/{device_token_id}：取消注册
- 用户隔离：不能操作他人设备
- 重复注册幂等：同一 token 覆盖更新
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app_factory import create_app

app = create_app()
client = TestClient(app)


def _login(username: str, password: str = "Secret123!") -> tuple[str, str]:
    register_response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert register_response.status_code in (200, 201)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_id": f"device-{username}"},
    )
    assert login_response.status_code == 200
    data = login_response.json()["data"]
    return data["access_token"], data["user"]["user_id"]


class TestDeviceRoutes:
    def test_register_device_success(self) -> None:
        token, _user_id = _login("alice-dev1")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/api/v1/devices",
            json={
                "device_token": "fcm_test_register_1",
                "platform": "android",
                "app_version": "1.0.0",
                "device_id": "android_test_001",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_token_id"]
        assert body["platform"] == "android"
        assert body["device_id"] == "android_test_001"
        assert body["registered_at"]

    def test_register_device_without_auth_fails(self) -> None:
        resp = client.post(
            "/api/v1/devices",
            json={
                "device_token": "fcm_noauth",
                "platform": "android",
                "device_id": "phone",
            },
        )
        assert resp.status_code in (401, 403)

    def test_list_devices_empty(self) -> None:
        token, _user_id = _login("alice-dev2")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/v1/devices", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_list_devices_after_register(self) -> None:
        token, _user_id = _login("alice-dev3")
        headers = {"Authorization": f"Bearer {token}"}
        # Register two devices
        client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_list_1", "platform": "android", "device_id": "phone"},
            headers=headers,
        )
        client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_list_2", "platform": "ios", "device_id": "tablet"},
            headers=headers,
        )
        resp = client.get("/api/v1/devices", headers=headers)
        assert resp.status_code == 200
        devices = resp.json()["data"]
        assert len(devices) == 2
        platforms = {d["platform"] for d in devices}
        assert platforms == {"android", "ios"}

    def test_device_isolation_between_users(self) -> None:
        alice_token, _ = _login("alice-dev4")
        bob_token, _ = _login("bob-dev4")

        client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_alice_only", "platform": "android", "device_id": "alice_phone"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        bob_resp = client.get(
            "/api/v1/devices",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert bob_resp.status_code == 200
        assert bob_resp.json()["data"] == []

    def test_duplicate_token_overwrites(self) -> None:
        token, _user_id = _login("alice-dev5")
        headers = {"Authorization": f"Bearer {token}"}

        r1 = client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_dup_token", "platform": "android", "device_id": "old_phone", "app_version": "1.0.0"},
            headers=headers,
        )
        assert r1.status_code == 200
        old_id = r1.json()["device_token_id"]

        r2 = client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_dup_token", "platform": "ios", "device_id": "new_phone", "app_version": "2.0.0"},
            headers=headers,
        )
        assert r2.status_code == 200
        new = r2.json()
        # Same token re-registered by same user updates the record
        assert new["platform"] == "ios"
        assert new["device_id"] == "new_phone"
        # device_token_id should be same (upsert, not create new)
        assert new["device_token_id"] == old_id

    def test_unregister_device_success(self) -> None:
        token, _user_id = _login("alice-dev6")
        headers = {"Authorization": f"Bearer {token}"}

        r = client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_del_token", "platform": "android", "device_id": "del_phone"},
            headers=headers,
        )
        device_id = r.json()["device_token_id"]

        del_resp = client.delete(f"/api/v1/devices/{device_id}", headers=headers)
        assert del_resp.status_code == 200

        list_resp = client.get("/api/v1/devices", headers=headers)
        assert list_resp.json()["data"] == []

    def test_unregister_other_user_device_fails(self) -> None:
        alice_token, _ = _login("alice-dev7")
        bob_token, _ = _login("bob-dev7")

        r = client.post(
            "/api/v1/devices",
            json={"device_token": "fcm_alice_unreg", "platform": "android", "device_id": "alice_phone"},
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        device_id = r.json()["device_token_id"]

        del_resp = client.delete(
            f"/api/v1/devices/{device_id}",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert del_resp.status_code in (403, 404)

    def test_unregister_nonexistent_device(self) -> None:
        token, _user_id = _login("alice-dev8")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.delete("/api/v1/devices/nonexistent-id", headers=headers)
        assert resp.status_code == 404

    def test_register_ios_device(self) -> None:
        token, _user_id = _login("alice-dev9")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/api/v1/devices",
            json={
                "device_token": "fcm_ios_test",
                "platform": "ios",
                "app_version": "2.1.0",
                "device_id": "iphone_xyz",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["platform"] == "ios"

    def test_register_web_device(self) -> None:
        token, _user_id = _login("alice-dev10")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/api/v1/devices",
            json={
                "device_token": "fcm_web_test",
                "platform": "web",
                "app_version": "3.0.0",
                "device_id": "browser_chrome",
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["platform"] == "web"

    def test_invalid_platform_rejected(self) -> None:
        token, _user_id = _login("alice-dev11")
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(
            "/api/v1/devices",
            json={"device_token": "t1", "platform": "windows", "device_id": "d1"},
            headers=headers,
        )
        assert resp.status_code == 422  # Validation error

    def test_missing_required_fields_rejected(self) -> None:
        token, _user_id = _login("alice-dev12")
        headers = {"Authorization": f"Bearer {token}"}
        # missing device_id
        resp = client.post(
            "/api/v1/devices",
            json={"device_token": "t1", "platform": "android"},
            headers=headers,
        )
        assert resp.status_code == 422
