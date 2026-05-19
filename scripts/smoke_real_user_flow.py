from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class UserSession:
    username: str
    password: str
    device_id: str
    user_id: str
    access_token: str
    refresh_token: str


def _request_json(
    *,
    method: str,
    base_url: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    expected_status: int | tuple[int, ...] = 200,
    timeout: int = 15,
) -> dict:
    body = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url=f"{base_url}{path}",
        data=body,
        headers=headers,
        method=method,
    )

    ok_statuses = (expected_status,) if isinstance(expected_status, int) else expected_status
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if resp.status not in ok_statuses:
                raise RuntimeError(f"{method} {path} expected {ok_statuses}, got {resp.status}: {data}")
            return {"status": resp.status, "data": data}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        detail = raw
        try:
            detail = json.loads(raw)
        except Exception:
            pass
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {detail}") from exc


def _log_step(title: str) -> None:
    print(f"\n[STEP] {title}")


def _wait_video_ready(
    *,
    base_url: str,
    token: str,
    video_id: str,
    timeout_sec: int = 180,
    poll_interval_sec: int = 2,
) -> None:
    deadline = time.time() + timeout_sec
    last_status: dict | None = None

    while time.time() < deadline:
        video_res = _request_json(
            method="GET",
            base_url=base_url,
            path=f"/api/v1/videos/{video_id}",
            token=token,
            expected_status=200,
        )
        payload = video_res.get("data", {}).get("data", {})

        # 以当前后端 schema 为准：transcribe_status / frame_extraction_status
        transcription_status = payload.get("transcribe_status")
        keyframe_status = payload.get("frame_extraction_status")

        # 兼容历史/别名字段
        if transcription_status is None:
            transcription_status = payload.get("transcription_status")
        if keyframe_status is None:
            keyframe_status = payload.get("keyframe_status")

        # 兼容部分后端字段命名
        if keyframe_status is None:
            keyframe_status = payload.get("keyframes_status")

        # 兼容布尔字段
        if transcription_status is None and "transcription_completed" in payload:
            transcription_status = "completed" if payload.get("transcription_completed") else "processing"
        if keyframe_status is None and "keyframe_extraction_completed" in payload:
            keyframe_status = "completed" if payload.get("keyframe_extraction_completed") else "processing"

        last_status = {
            "transcription_status": transcription_status,
            "keyframe_status": keyframe_status,
        }

        transcription_status_norm = str(transcription_status or "").upper()
        keyframe_status_norm = str(keyframe_status or "").upper()

        if transcription_status_norm == "COMPLETED" and keyframe_status_norm == "COMPLETED":
            print("[OK] video preprocessing completed")
            return

        if transcription_status_norm == "FAILED" or keyframe_status_norm == "FAILED":
            raise RuntimeError(f"Video preprocessing failed: {last_status}")

        oss_key = payload.get("oss_key")
        if not (isinstance(oss_key, str) and oss_key.strip()):
            print(
                "[WAIT] video has no oss_key yet (likely not finalized upload), "
                f"status={last_status}"
            )
        else:
            print(f"[WAIT] video not ready yet: {last_status}")
        time.sleep(poll_interval_sec)

    raise TimeoutError(
        f"Wait video ready timeout after {timeout_sec}s, last status={last_status}"
    )


def _register_and_login(base_url: str, prefix: str, suffix: str) -> UserSession:
    username = f"{prefix}_{suffix}"
    password = "Pass123456!"
    device_id = f"android-{prefix}-{suffix}"

    _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/auth/register",
        payload={"username": username, "password": password},
        expected_status=200,
    )

    login_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/auth/login",
        payload={"username": username, "password": password, "device_id": device_id},
        expected_status=200,
    )
    token_data = login_res["data"]["data"]
    return UserSession(
        username=username,
        password=password,
        device_id=device_id,
        user_id=token_data["user"]["user_id"],
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for real user register/access flow")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    suffix = str(int(time.time()))

    _log_step("Health check")
    health = _request_json(method="GET", base_url=base_url, path="/health", expected_status=200)
    if health["data"].get("status") != "ok":
        raise RuntimeError(f"Unexpected health payload: {health['data']}")
    print("[OK] /health")

    _log_step("Register + login user A/B")
    user_a = _register_and_login(base_url, "user_a", suffix)
    user_b = _register_and_login(base_url, "user_b", suffix)
    print(f"[OK] user_a={user_a.username}, user_b={user_b.username}")

    _log_step("Validate /auth/me for user A")
    me_a = _request_json(
        method="GET",
        base_url=base_url,
        path="/api/v1/auth/me",
        token=user_a.access_token,
        expected_status=200,
    )
    if me_a["data"]["data"]["username"] != user_a.username:
        raise RuntimeError("/auth/me username mismatch for user A")
    print("[OK] /auth/me")

    _log_step("Create KB + video + bind + summary task for user A")
    kb_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/kbs",
        token=user_a.access_token,
        payload={
            "name": f"联调知识库_{suffix}",
            "category": "demo",
            "description": "smoke test",
            "config": {
                "retrieval": {"top_k": 5, "rerank": True},
                "tool_preferences": {"allow_web_search": False},
                "llm_policy": {"temperature": 0.2},
            },
        },
        expected_status=201,
    )
    kbid = kb_res["data"]["data"]["kbid"]

    video_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/videos",
        token=user_a.access_token,
        payload={"file_name": f"smoke_video_{suffix}.mp4"},
        expected_status=201,
    )
    video_id = video_res["data"]["data"]["video_id"]

    _request_json(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/kbs/{kbid}/videos",
        token=user_a.access_token,
        payload={"video_id": video_id},
        expected_status=200,
    )

    _log_step("Wait video preprocessing ready")
    _wait_video_ready(
        base_url=base_url,
        token=user_a.access_token,
        video_id=video_id,
    )

    task_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/tasks",
        token=user_a.access_token,
        payload={
            "kbid": kbid,
            "video_id": video_id,
            "user_initial_preference": "请突出业务结论",
        },
        expected_status=201,
    )
    task_id = task_res["data"]["data"]["task_id"]
    print(f"[OK] kbid={kbid}, video_id={video_id}, task_id={task_id}")

    _log_step("Verify user isolation (user B cannot access user A resource)")
    try:
        _request_json(
            method="GET",
            base_url=base_url,
            path=f"/api/v1/kbs/{kbid}",
            token=user_b.access_token,
            expected_status=200,
        )
        raise RuntimeError("Isolation check failed: user B unexpectedly accessed user A KB")
    except RuntimeError as exc:
        msg = str(exc)
        if "failed with 404" not in msg and "failed with 403" not in msg:
            raise
        print("[OK] isolation check (expected 404/403)")

    _log_step("Refresh token and re-validate /auth/me")
    refresh_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/auth/refresh",
        payload={"refresh_token": user_a.refresh_token, "device_id": user_a.device_id},
        expected_status=200,
    )
    new_access = refresh_res["data"]["data"]["access_token"]
    _request_json(
        method="GET",
        base_url=base_url,
        path="/api/v1/auth/me",
        token=new_access,
        expected_status=200,
    )
    print("[OK] refresh token flow")

    print("\n[DONE] Smoke flow passed")
    print(json.dumps({
        "user_a": user_a.username,
        "user_b": user_b.username,
        "kbid": kbid,
        "video_id": video_id,
        "task_id": task_id,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
