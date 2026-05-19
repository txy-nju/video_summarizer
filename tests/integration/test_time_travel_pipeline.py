import os
import unittest
from unittest.mock import patch, MagicMock

from core.workflow.api import answer_question_at_timestamp


class _FakeCheckpointer:
    def __init__(self, checkpoint):
        self._checkpoint = checkpoint

    def get(self, _config):
        return self._checkpoint


class TestTimeTravelPipelineIntegration(unittest.TestCase):
    def test_answer_question_success_with_checkpoint_and_llm(self):
        """集成路径：checkpoint 命中 + LLM 可用时，应返回模型回答文本"""
        checkpoint = {
            "channel_values": {
                "transcript": '{"segments": [{"start": 9, "end": 13, "text": "target evidence"}]}',
                "keyframes": [
                    {"time": "00:08", "image": ""},
                    {"time": "00:12", "image": ""},
                ],
                "draft_summary": "历史总结草稿",
                "user_prompt": "请关注技术细节",
            }
        }

        with patch("core.workflow.api.create_checkpointer", return_value=_FakeCheckpointer(checkpoint)):
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key"}, clear=False):
                with patch("core.workflow.api.get_model_for_capability") as mock_get_model:
                    mock_model = MagicMock()
                    mock_model.chat_completion.return_value = "这是追问回答"
                    mock_get_model.return_value = mock_model

                    result = answer_question_at_timestamp(
                        thread_id="thread-1",
                        timestamp="00:10",
                        question="这段在讲什么？",
                        window_seconds=10,
                    )

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        self.assertTrue(messages, "LLM 调用的 messages 不应为空")
        user_message_content = messages[-1]["content"][0]["text"]
        self.assertIn("target evidence", user_message_content, "必须向大模型提供目标时间段的文本证据")
        self.assertIn("这段在讲什么？", user_message_content, "必须向大模型传递用户的提问")
        self.assertIn("00:10", user_message_content, "应当告知大模型当前追问的时间点")
        self.assertEqual(result, "这是追问回答")

    def test_answer_question_thread_not_found(self):
        """集成路径：checkpoint 未命中时应返回可解释提示"""
        with patch("core.workflow.api.create_checkpointer", return_value=_FakeCheckpointer(None)):
            result = answer_question_at_timestamp(
                thread_id="missing-thread",
                timestamp="00:10",
                question="这段在讲什么？",
            )

        self.assertIn("未找到 thread_id=missing-thread", result)

    def test_answer_question_fallback_without_openai_key(self):
        """集成路径：无 OPENAI_API_KEY 时应走证据降级输出"""
        checkpoint = {
            "channel_values": {
                "transcript": '{"segments": [{"start": 30, "end": 35, "text": "window evidence"}]}',
                "keyframes": [{"time": "00:33", "image": ""}],
                "draft_summary": "历史草稿",
                "user_prompt": "请简洁",
            }
        }

        with patch("core.workflow.api.create_checkpointer", return_value=_FakeCheckpointer(checkpoint)):
            with patch.dict(os.environ, {}, clear=True):
                result = answer_question_at_timestamp(
                    thread_id="thread-2",
                    timestamp="00:32",
                    question="这个时间点的重点是什么？",
                )

        self.assertIn("[系统降级回答]", result)
        self.assertIn("目标时间戳: 00:32", result)
        self.assertIn("语音证据", result)

    def test_answer_question_api_timeout_fallback(self):
        """集成路径：OpenAI API 异常/超时应当被捕获并降级返回"""
        checkpoint = {
            "channel_values": {
                "transcript": '{"segments": [{"start": 30, "end": 35, "text": "window evidence"}]}',
                "keyframes": [{"time": "00:33", "image": ""}],
                "draft_summary": "历史草稿",
                "user_prompt": "请简洁",
            }
        }

        with patch("core.workflow.api.create_checkpointer", return_value=_FakeCheckpointer(checkpoint)):
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key"}, clear=False):
                with patch("core.workflow.api.get_model_for_capability") as mock_get_model:
                    mock_model = MagicMock()
                    mock_model.chat_completion.side_effect = Exception("OpenAI API Timeout")
                    mock_get_model.return_value = mock_model

                    result = answer_question_at_timestamp(
                        thread_id="thread-timeout",
                        timestamp="00:32",
                        question="这个时间点的重点是什么？",
                    )

        self.assertIn("[系统降级回答]", result)
        self.assertIn("OpenAI API 调用异常", result)

    def test_answer_question_invalid_arguments(self):
        """集成路径：非法参数应抛 ValueError，避免静默失败"""
        with self.assertRaises(ValueError):
            answer_question_at_timestamp(thread_id="", timestamp="00:10", question="q")

        with self.assertRaises(ValueError):
            answer_question_at_timestamp(thread_id="t1", timestamp="00:10", question="")

        with self.assertRaises(ValueError):
            answer_question_at_timestamp(thread_id="t1", timestamp="bad-ts", question="q")


if __name__ == "__main__":
    unittest.main()
