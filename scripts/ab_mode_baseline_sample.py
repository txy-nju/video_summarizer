import json
import statistics
import os
import sys
from typing import Any, Dict, List
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.workflow.api import analyze_video


class _FakeWorkflowApp:
    def stream(self, initial_state, config, stream_mode="updates"):
        yield {
            "chunk_audio_worker_node": {
                "chunk_results": [
                    {"chunk_id": "chunk-000", "audio_insights": "audio-send", "latency_ms": {"audio": 4}}
                ]
            }
        }

        yield {
            "chunk_vision_worker_node": {
                "chunk_results": [
                    {"chunk_id": "chunk-000", "vision_insights": "vision", "latency_ms": {"vision": 5}}
                ]
            }
        }
        yield {
            "chunk_subgraph_node": {
                "chunk_results": [
                    {
                        "chunk_id": "chunk-000",
                        "audio_insights": "audio",
                        "vision_insights": "vision",
                        "chunk_summary": "summary",
                    }
                ]
            }
        }
        yield {"fusion_drafter_node": {"draft_summary": "final summary", "revision_count": 1}}


def _run_send_api(iterations: int = 30) -> Dict[str, Any]:
    workflow_durations: List[int] = []
    final_state_sizes: List[int] = []
    failures: List[str] = []

    def _collect_metric(_logger, event: str, **fields: Any):
        if event == "workflow_finished":
            workflow_durations.append(int(fields.get("total_duration_ms", 0)))
            final_state_sizes.append(int(fields.get("final_state_estimate_bytes", 0)))

    for i in range(iterations):
        try:
            with patch("core.workflow.api.create_checkpointer", return_value=object()):
                with patch("core.workflow.api.build_video_summary_graph", return_value=_FakeWorkflowApp()):
                    with patch("core.workflow.api.ENABLE_METRICS_LOGGING", True):
                        with patch("core.workflow.api.METRICS_SAMPLE_RATE", 1.0):
                            with patch("core.workflow.api.log_metric_event", side_effect=_collect_metric):
                                analyze_video(
                                    transcript='{"segments": [{"start": 0, "end": 1, "text": "hello"}]}',
                                    keyframes=[
                                        {"time": "00:00", "image": "x"},
                                        {"time": "00:02", "image": "y"},
                                    ],
                                    user_prompt="focus",
                                    thread_id=f"ab-send_api-{i}",
                                )
        except Exception as exc:
            failures.append(str(exc))

    success_count = iterations - len(failures)
    success_rate = (success_count / iterations) * 100 if iterations else 0.0

    return {
        "mode": "send_api",
        "iterations": iterations,
        "success_count": success_count,
        "failure_count": len(failures),
        "success_rate_pct": round(success_rate, 2),
        "workflow_duration_ms_avg": round(statistics.mean(workflow_durations), 2) if workflow_durations else None,
        "workflow_duration_ms_p95": round(statistics.quantiles(workflow_durations, n=20)[18], 2)
        if len(workflow_durations) >= 20
        else (max(workflow_durations) if workflow_durations else None),
        "final_state_estimate_bytes_avg": round(statistics.mean(final_state_sizes), 2) if final_state_sizes else None,
        "sample_failures": failures[:3],
    }


def main() -> None:
    send_api = _run_send_api(iterations=30)
    print(json.dumps({"send_api": send_api}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

