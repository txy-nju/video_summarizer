"""
文件上传路由测试（步骤 5.5 TUS 协议）。

覆盖：
- POST /api/v1/uploads：初始化上传会话
- GET /api/v1/uploads/{upload_id}：查询进度
- PATCH /api/v1/uploads/{upload_id}：上传分片
- HEAD /api/v1/uploads/{upload_id}：TUS 兼容查询
- DELETE /api/v1/uploads/{upload_id}：取消上传
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.app_factory import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """注册新用户并返回认证 headers。"""
    username = f"upload_test_{uuid.uuid4().hex[:8]}"
    password = "TestUpload123!"
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "device_id": "test_device_001"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestInitiateUpload:
    def test_initiate_upload_returns_session(self, client, auth_headers):
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "test_video.mp4", "total_size": 104857600},  # 100MB
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "upload_id" in data
        assert data["chunk_size"] == 10485760  # 10 MiB
        assert "expires_at" in data

    def test_initiate_upload_rejects_missing_auth(self, client):
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "test.mp4", "total_size": 1000},
        )
        assert resp.status_code == 401

    def test_initiate_upload_rejects_zero_size(self, client, auth_headers):
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "test.mp4", "total_size": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_initiate_upload_rejects_empty_filename(self, client, auth_headers):
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "", "total_size": 1000},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestUploadChunk:
    def test_upload_single_chunk_completes(self, client, auth_headers):
        # Initiate
        total_size = 5 * 1024 * 1024  # 5MB (less than one chunk)
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "small.mp4", "total_size": total_size},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        upload_id = resp.json()["upload_id"]

        # Upload single chunk (offset 0)
        chunk_data = b"x" * total_size
        resp = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=chunk_data,
            headers={
                **auth_headers,
                "Upload-Offset": "0",
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert resp.status_code == 200  # complete → 200
        assert "Upload-Offset" in resp.headers

    def test_upload_partial_then_query_progress(self, client, auth_headers):
        # Initiate (total: 30MB, chunk: 10MB → 3 chunks)
        total_size = 30 * 1024 * 1024
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "medium.mp4", "total_size": total_size},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        upload_id = resp.json()["upload_id"]

        # Upload first chunk (10MB)
        chunk_size = 10 * 1024 * 1024
        resp = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=b"a" * chunk_size,
            headers={
                **auth_headers,
                "Upload-Offset": "0",
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert resp.status_code == 204  # not yet complete

        # Query progress
        resp = client.get(
            f"/api/v1/uploads/{upload_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["uploaded_size"] == chunk_size
        assert data["total_size"] == total_size
        assert data["uploaded_chunks"] == [0]

    def test_upload_wrong_chunk_size_rejected(self, client, auth_headers):
        total_size = 20 * 1024 * 1024
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "test.mp4", "total_size": total_size},
            headers=auth_headers,
        )
        upload_id = resp.json()["upload_id"]

        # Send wrong-sized chunk (should be 10MB, send 5MB at offset 0)
        resp = client.patch(
            f"/api/v1/uploads/{upload_id}",
            content=b"y" * (5 * 1024 * 1024),
            headers={
                **auth_headers,
                "Upload-Offset": "0",
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert resp.status_code == 400

    def test_upload_to_wrong_user_rejected(self, client, auth_headers):
        # User A creates upload
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "secret.mp4", "total_size": 10 * 1024 * 1024},
            headers=auth_headers,
        )
        upload_id = resp.json()["upload_id"]

        # User B tries to access
        other_username = f"other_{uuid.uuid4().hex[:8]}"
        resp2 = client.post(
            "/api/v1/auth/register",
            json={"username": other_username, "password": "Other123!"},
        )
        resp2 = client.post(
            "/api/v1/auth/login",
            json={"username": other_username, "password": "Other123!", "device_id": "other_device"},
        )
        other_token = resp2.json()["data"]["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}

        resp = client.get(f"/api/v1/uploads/{upload_id}", headers=other_headers)
        assert resp.status_code == 404  # Returns 404 to avoid leaking existence


class TestTusHead:
    def test_head_returns_tus_headers(self, client, auth_headers):
        total_size = 10 * 1024 * 1024
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "tus_test.mp4", "total_size": total_size},
            headers=auth_headers,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.head(f"/api/v1/uploads/{upload_id}", headers=auth_headers)
        assert resp.status_code == 204
        assert resp.headers["Tus-Resumable"] == "1.0.0"
        assert resp.headers["Upload-Offset"] == "0"
        assert resp.headers["Upload-Length"] == str(total_size)


class TestCancelUpload:
    def test_cancel_upload(self, client, auth_headers):
        resp = client.post(
            "/api/v1/uploads",
            json={"file_name": "cancel_me.mp4", "total_size": 10 * 1024 * 1024},
            headers=auth_headers,
        )
        upload_id = resp.json()["upload_id"]

        resp = client.delete(f"/api/v1/uploads/{upload_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Verify it's gone
        resp = client.get(f"/api/v1/uploads/{upload_id}", headers=auth_headers)
        assert resp.status_code == 404
