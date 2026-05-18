from __future__ import annotations

import pytest

from backend.tasks import workflow_runtime_tasks as runtime_tasks


class _DummyService:
    async def start_analysis_workflow_async(self, **kwargs):
        return {"workflow_state": "WAITING_USER_APPROVAL", "thread_id": kwargs["task_id"]}

    async def start_finalization_workflow_async(self, **kwargs):
        return "final-summary"

    async def start_time_travel_qa_async(self, **kwargs):
        return "time-travel-answer"


def test_async_execute_analysis_workflow_success(monkeypatch):
    monkeypatch.setattr(runtime_tasks, "_build_orchestration_service", lambda: _DummyService())

    result = runtime_tasks.async_execute_analysis_workflow.run(
        owner_id="u1",
        task_id="t1",
        transcript="hello",
        keyframes=[],
        user_initial_preference="pref",
        trace_id="r1",
    )

    assert result["workflow_state"] == "WAITING_USER_APPROVAL"
    assert result["thread_id"] == "t1"


def test_async_execute_finalization_workflow_success(monkeypatch):
    monkeypatch.setattr(runtime_tasks, "_build_orchestration_service", lambda: _DummyService())

    result = runtime_tasks.async_execute_finalization_workflow.run(
        owner_id="u1",
        task_id="t1",
        edited_aggregated_chunk_insights="edited",
        human_guidance="guide",
        trace_id="r1",
    )

    assert result["workflow_state"] == "COMPLETED"
    assert result["final_summary"] == "final-summary"


def test_async_execute_time_travel_qa_success(monkeypatch):
    monkeypatch.setattr(runtime_tasks, "_build_orchestration_service", lambda: _DummyService())

    result = runtime_tasks.async_execute_time_travel_qa.run(
        owner_id="u1",
        task_id="t1",
        timestamp="00:10:00",
        question="what happened",
        window_seconds=20,
        trace_id="r1",
    )

    assert result["answer"] == "time-travel-answer"
    assert result["timestamp"] == "00:10:00"


def test_async_execute_analysis_workflow_retries_on_unexpected_error(monkeypatch):
    class _FailingService:
        async def start_analysis_workflow_async(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(runtime_tasks, "_build_orchestration_service", lambda: _FailingService())

    captured: dict[str, object] = {}

    class _RetrySignal(Exception):
        pass

    def _fake_retry(*, exc, countdown):
        captured["exc"] = exc
        captured["countdown"] = countdown
        raise _RetrySignal()

    monkeypatch.setattr(runtime_tasks.async_execute_analysis_workflow, "retry", _fake_retry)

    with pytest.raises(_RetrySignal):
        runtime_tasks.async_execute_analysis_workflow.run(
            owner_id="u1",
            task_id="t1",
            transcript="hello",
            keyframes=[],
            user_initial_preference="pref",
            trace_id="r1",
        )

    assert isinstance(captured["exc"], RuntimeError)
    assert captured["countdown"] == 60
