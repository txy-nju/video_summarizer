from __future__ import annotations

import argparse
import json
import os
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


def _request_binary(
    *,
    method: str,
    base_url: str,
    path: str,
    token: str,
    body: bytes,
    headers: dict[str, str],
    expected_status: int | tuple[int, ...],
    timeout: int = 30,
) -> dict:
    req_headers = dict(headers)
    req_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url=f"{base_url}{path}",
        data=body,
        headers=req_headers,
        method=method,
    )
    ok_statuses = (expected_status,) if isinstance(expected_status, int) else expected_status
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in ok_statuses:
                raise RuntimeError(f"{method} {path} expected {ok_statuses}, got {resp.status}")
            _ = resp.read()
            return {"status": resp.status, "headers": dict(resp.headers.items())}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {raw}") from exc


def _request_sse(
    *,
    method: str,
    base_url: str,
    path: str,
    token: str,
    payload: dict,
    timeout: int = 120,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"{method} {path} expected 200, got {resp.status}")

            events: list[dict] = []
            current_event = "message"
            data_lines: list[str] = []

            for raw_line in resp:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line:
                    if data_lines:
                        data_text = "\n".join(data_lines)
                        try:
                            data_payload = json.loads(data_text)
                        except json.JSONDecodeError:
                            data_payload = {"raw": data_text}
                        event_obj = {"event": current_event, "data": data_payload}
                        events.append(event_obj)
                        if current_event in ("done", "error"):
                            return {"status": resp.status, "events": events}
                    current_event = "message"
                    data_lines = []
                    continue

                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())

            return {"status": resp.status, "events": events}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {raw}") from exc


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


def _upload_local_video_via_tus(
    *,
    base_url: str,
    token: str,
    local_video_path: Path,
    upload_file_name: str,
) -> str:
    total_size = local_video_path.stat().st_size
    init_res = _request_json(
        method="POST",
        base_url=base_url,
        path="/api/v1/uploads",
        token=token,
        payload={
            "file_name": upload_file_name,
            "total_size": total_size,
        },
        expected_status=201,
        timeout=30,
    )
    init_data = init_res["data"]
    upload_id = init_data["upload_id"]
    chunk_size = int(init_data["chunk_size"])
    print(f"[OK] initiated upload: upload_id={upload_id}, chunk_size={chunk_size}, total_size={total_size}")

    offset = 0
    with local_video_path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            result = _request_binary(
                method="PATCH",
                base_url=base_url,
                path=f"/api/v1/uploads/{upload_id}",
                token=token,
                body=chunk,
                headers={
                    "Upload-Offset": str(offset),
                    "Tus-Resumable": "1.0.0",
                    "Content-Type": "application/offset+octet-stream",
                },
                expected_status=(200, 204),
                timeout=60,
            )
            next_offset = int(result["headers"].get("Upload-Offset", offset + len(chunk)))
            offset = next_offset
            print(f"[UPLOAD] offset={offset}/{total_size}")

    if offset != total_size:
        raise RuntimeError(f"Upload offset mismatch: offset={offset}, expected={total_size}")
    print("[OK] all chunks uploaded")
    return upload_id


def _wait_video_created_by_file_name(
    *,
    base_url: str,
    token: str,
    file_name: str,
    timeout_sec: int = 180,
    poll_interval_sec: int = 2,
) -> str:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = _request_json(
            method="GET",
            base_url=base_url,
            path="/api/v1/videos?page=1&page_size=100",
            token=token,
            expected_status=200,
            timeout=20,
        )
        items = res.get("data", {}).get("data", [])
        matches = [x for x in items if x.get("file_name") == file_name]
        for item in reversed(matches):
            oss_key = item.get("oss_key")
            if isinstance(oss_key, str) and oss_key.strip():
                video_id = item["video_id"]
                print(f"[OK] video materialized from upload: video_id={video_id}, oss_key={oss_key}")
                return video_id
        print("[WAIT] uploaded file not materialized to video resource yet")
        time.sleep(poll_interval_sec)

    raise TimeoutError(f"Wait uploaded video materialization timeout after {timeout_sec}s, file_name={file_name}")


def _wait_task_state(
    *,
    base_url: str,
    token: str,
    task_id: str,
    target_states: set[str],
    timeout_sec: int = 300,
    poll_interval_sec: int = 2,
) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = _request_json(
            method="GET",
            base_url=base_url,
            path=f"/api/v1/tasks/{task_id}",
            token=token,
            expected_status=200,
            timeout=20,
        )
        task_data = res.get("data", {}).get("data", {})
        state = str(task_data.get("workflow_state") or "").upper()
        if state in target_states:
            print(f"[OK] task reached state={state}")
            return task_data
        if state == "FAILED":
            raise RuntimeError(f"Task failed: task_id={task_id}")
        print(f"[WAIT] task workflow_state={state}")
        time.sleep(poll_interval_sec)
    raise TimeoutError(
        f"Wait task state timeout after {timeout_sec}s, task_id={task_id}, target_states={sorted(target_states)}"
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
    parser.add_argument("--video-path", required=True, help="Path to a local real video file")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    suffix = str(int(time.time()))
    local_video_path = Path(args.video_path).expanduser().resolve()
    if not local_video_path.exists() or not local_video_path.is_file():
        raise FileNotFoundError(f"Local video not found: {local_video_path}")

    upload_file_name = f"smoke_{suffix}_{local_video_path.name}"

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

    _log_step("Create KB for user A")
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
    print(f"[OK] kbid={kbid}")

    _log_step("Upload local video via TUS chunks")
    _ = _upload_local_video_via_tus(
        base_url=base_url,
        token=user_a.access_token,
        local_video_path=local_video_path,
        upload_file_name=upload_file_name,
    )

    _log_step("Wait uploaded video to materialize and become ready")
    video_id = _wait_video_created_by_file_name(
        base_url=base_url,
        token=user_a.access_token,
        file_name=upload_file_name,
    )

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
        timeout_sec=600,
    )

    _log_step("Query KB and linked videos")
    _request_json(
        method="GET",
        base_url=base_url,
        path=f"/api/v1/kbs/{kbid}",
        token=user_a.access_token,
        expected_status=200,
    )
    kb_videos_res = _request_json(
        method="GET",
        base_url=base_url,
        path=f"/api/v1/kbs/{kbid}/videos?page=1&page_size=20",
        token=user_a.access_token,
        expected_status=200,
    )
    print(f"[OK] kb linked videos count={len(kb_videos_res['data']['data'])}")

    _log_step("Create summary task")
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
    print(f"[OK] created task_id={task_id}")

    _log_step("Trigger analysis workflow and wait for human approval state")
    _request_json(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/tasks/{task_id}/start-analysis",
        token=user_a.access_token,
        payload={},
        expected_status=202,
    )
    _ = _wait_task_state(
        base_url=base_url,
        token=user_a.access_token,
        task_id=task_id,
        target_states={"WAITING_USER_APPROVAL"},
    )

    _log_step("Approve and finalize summary workflow")
    _request_json(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/tasks/{task_id}/approve-and-finalize",
        token=user_a.access_token,
        payload={
            "edited_aggregated_chunk_insights": "",
            "human_guidance": "请给出业务导向的最终总结，并提炼关键结论。",
        },
        expected_status=202,
    )
    completed_task = _wait_task_state(
        base_url=base_url,
        token=user_a.access_token,
        task_id=task_id,
        target_states={"COMPLETED"},
    )
    final_summary = completed_task.get("final_summary")
    if not (isinstance(final_summary, str) and final_summary.strip()):
        raise RuntimeError("Final summary is empty after workflow completed")
    print(f"[OK] final summary generated, length={len(final_summary)}")

    _log_step("Run video time-travel follow-up QA stream")
    video_qa_sse = _request_sse(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/tasks/{task_id}/time-travel-qa/stream",
        token=user_a.access_token,
        payload={
            "timestamp": "00:00:10",
            "question_content": "请基于该时间点附近内容，提炼一个可执行建议。",
            "window_seconds": 30,
            "attachments": [],
        },
        timeout=180,
    )
    video_qa_events = video_qa_sse.get("events", [])
    if not any(e.get("event") == "done" for e in video_qa_events):
        raise RuntimeError(f"Video QA stream did not finish with done event: events={video_qa_events}")
    print(f"[OK] video QA stream done, events={len(video_qa_events)}")

    _log_step("Create global chat and run KB-level global QA stream")
    chat_res = _request_json(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/kbs/{kbid}/chats",
        token=user_a.access_token,
        payload={
            "kbid": kbid,
            "chat_title": f"smoke_global_chat_{suffix}",
        },
        expected_status=201,
    )
    chat_id = chat_res["data"]["data"]["chat_id"]

    global_qa_sse = _request_sse(
        method="POST",
        base_url=base_url,
        path=f"/api/v1/kbs/{kbid}/chats/{chat_id}/qa/stream",
        token=user_a.access_token,
        payload={
            "question_content": "请基于当前知识库视频内容，给出三个核心业务要点。",
            "attachments": [],
        },
        timeout=180,
    )
    global_qa_events = global_qa_sse.get("events", [])
    if not any(e.get("event") == "done" for e in global_qa_events):
        raise RuntimeError(f"Global QA stream did not finish with done event: events={global_qa_events}")
    print(f"[OK] global QA stream done, events={len(global_qa_events)}")

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
        "chat_id": chat_id,
        "video_id": video_id,
        "task_id": task_id,
        "upload_file_name": upload_file_name,
        "video_path": os.fspath(local_video_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
