import unittest
from typing import cast
from unittest.mock import AsyncMock, patch

from core.workflow.video_summary.nodes.data_preparation_node import data_preparation_node
from core.workflow.video_summary.state import VideoSummaryState


class TestDataPreparationNode(unittest.TestCase):
    @patch("core.workflow.video_summary.nodes.data_preparation_node._record_observable_event")
    @patch(
        "core.workflow.video_summary.nodes.data_preparation_node._fetch_keyframe_bytes",
        new_callable=AsyncMock,
    )
    def test_returns_recoverable_degraded_status_and_event_when_prefetch_partial_failed(
        self,
        mock_fetch,
        mock_record_event,
    ):
        mock_fetch.side_effect = [b"ok", None]
        state = cast(
            VideoSummaryState,
            {
                "thread_id": "task-123",
                "keyframes": [
                    {"oss_key": "frames/0.jpg", "timestamp": 1.0},
                    {"oss_key": "frames/1.jpg", "timestamp": 2.0},
                ],
            },
        )

        result = data_preparation_node(state)

        status = result.get("data_preparation_status", {})
        self.assertEqual(status.get("status"), "degraded")
        self.assertEqual(status.get("fetched"), 1)
        self.assertEqual(status.get("total"), 2)

        error = status.get("error", {})
        self.assertEqual(error.get("code"), "DATA_PREPARATION_DEGRADED")
        self.assertTrue(error.get("is_retryable"))
        self.assertEqual(error.get("retry_after"), 5)

        events = result.get("data_preparation_events", [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("status"), "DEGRADED")
        self.assertEqual(events[0].get("scope_id"), "task-123")
        mock_record_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
