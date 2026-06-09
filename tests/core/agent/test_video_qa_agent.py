"""Tests for VideoQAAgent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.agent.video_qa_agent import VideoQAAgent
from core.memory.base import BaseChatMemory


class _FakeChatMemory(BaseChatMemory):
    """In-memory chat memory for testing."""

    def __init__(self):
        self._turns: list[dict] = []

    def build_messages(self, *, chat_id, owner_id, system_prompt,
                       current_question, rag_context="", max_tokens=None):
        messages = [{"role": "system", "content": system_prompt}]
        for turn in self._turns:
            messages.append({"role": "user", "content": turn["question"]})
            messages.append({"role": "assistant", "content": turn["answer"]})
        if current_question:
            messages.append({"role": "user", "content": current_question})
        return messages

    def add_turn(self, *, chat_id, question, answer):
        self._turns.append({"question": question, "answer": answer})

    def clear(self, *, chat_id):
        self._turns.clear()


class TestExecutionContextExtension:
    """Task 7.3: ExecutionContext extension."""

    def test_default_empty_lists(self):
        from core.context.execution_context import ExecutionContext
        ctx = ExecutionContext(owner_id="u1", kbid="k1")
        assert ctx.attachments == []
        assert ctx.frames == []

    def test_with_attachments(self):
        from core.context.execution_context import ExecutionContext
        ctx = ExecutionContext(
            owner_id="u1", kbid="k1",
            attachments=[{"mime_type": "image/png", "frame_path": "/tmp/img.png"}],
        )
        assert len(ctx.attachments) == 1
        assert ctx.attachments[0]["mime_type"] == "image/png"

    def test_with_frames(self):
        from core.context.execution_context import ExecutionContext
        ctx = ExecutionContext(
            owner_id="u1", kbid="k1",
            frames=[{"frame_path": "/tmp/f.jpg", "time_range": "00:01:23"}],
        )
        assert len(ctx.frames) == 1
        assert ctx.frames[0]["time_range"] == "00:01:23"


class TestVideoQAAgentRAGMode:
    """Task 7.1: RAG mode tests."""

    @pytest.fixture
    def mock_deps(self):
        memory = _FakeChatMemory()
        rag_svc = MagicMock()
        rag_svc._resolve_video_collection.return_value = "video_test123"
        fake_results = [MagicMock(text="Chunk 1 text"), MagicMock(text="Chunk 2 text")]
        rag_svc._build_retrieval_context.return_value = MagicMock(
            results=fake_results,
            frames=[],
            cited_sources=[{"video_id": "v1", "quote": "Chunk 1"}],
        )
        rag_svc._download_attachment_frames.return_value = []

        llm = MagicMock()
        llm._model_name = "test-model"
        llm._model.stream_chat_completion.return_value = iter(["回答: ", "这是测试回复"])

        agent = VideoQAAgent(
            memory=memory,
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
        )
        return agent, memory, rag_svc, llm

    def test_answer_returns_string(self, mock_deps):
        agent, *_ = mock_deps
        result = agent.answer(
            question="测试问题", chat_id="task-1", kbid="", owner_id="user-1"
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_answer_stream_yields_tokens(self, mock_deps):
        agent, *_ = mock_deps
        items = list(agent.answer_stream(
            question="测试问题", chat_id="task-1", kbid="", owner_id="user-1"
        ))
        text_tokens = [t for t in items if isinstance(t, str)]
        assert len(text_tokens) > 0
        assert "测试回复" in "".join(text_tokens)

    def test_last_cited_sources_populated(self, mock_deps):
        agent, *_ = mock_deps
        list(agent.answer_stream(
            question="测试问题", chat_id="task-1", kbid="", owner_id="user-1"
        ))
        assert len(agent.last_cited_sources) == 1
        assert agent.last_cited_sources[0]["video_id"] == "v1"

    def test_no_results_returns_fallback(self, mock_deps):
        agent, _, rag_svc, _ = mock_deps
        rag_svc._build_retrieval_context.return_value = MagicMock(
            results=[],
            frames=[],
            cited_sources=[],
        )
        items = agent.answer_stream(
            question="测试问题", chat_id="task-1", kbid="", owner_id="user-1"
        )
        result = "".join(t for t in items if isinstance(t, str))
        assert "未找到" in result

    def test_multiturn_memory_injection(self, mock_deps):
        agent, memory, *_ = mock_deps
        # First turn
        list(agent.answer_stream(
            question="第一个问题", chat_id="task-1", kbid="", owner_id="user-1"
        ))
        memory.add_turn(chat_id="task-1", question="第一个问题", answer="第一个回答")

        # Second turn — history should include first turn
        messages = memory.build_messages(
            chat_id="task-1", owner_id="user-1",
            system_prompt="test", current_question="第二个问题",
        )
        # Should contain: system, user(q1), assistant(a1), user(q2)
        assert len(messages) == 4
        assert messages[1]["content"] == "第一个问题"
        assert messages[2]["content"] == "第一个回答"


class TestVideoQAAgentTimestampMode:
    """Task 7.1: Timestamp mode tests."""

    @pytest.fixture
    def mock_deps(self):
        memory = _FakeChatMemory()
        rag_svc = MagicMock()
        rag_svc._download_attachment_frames.return_value = []

        llm = MagicMock()
        llm._model_name = "test-model"
        llm._model.stream_chat_completion.return_value = iter(["证据显示: ", "答案"])

        agent = VideoQAAgent(
            memory=memory,
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
        )
        return agent, memory

    def test_timestamp_mode_no_checkpoint(self, mock_deps):
        agent, _ = mock_deps
        with patch(
            "core.workflow.checkpoint_factory.create_checkpointer"
        ) as mock_ckpt:
            mock_ckpt.return_value.get.return_value = None
            items = agent.answer_stream_with_context(
                question="当时发生了什么？",
                chat_id="task-1",
                owner_id="user-1",
                mode="timestamp",
                timestamp="00:05:00",
                window_seconds=20,
            )
            result = "".join(t for t in items if isinstance(t, str))
            assert "未找到" in result or "历史会话" in result

    def test_timestamp_mode_no_api_key(self, mock_deps):
        agent, _ = mock_deps
        with patch(
            "core.workflow.checkpoint_factory.create_checkpointer"
        ) as mock_ckpt, patch(
            "core.llm.config.resolve_api_key", return_value=""
        ):
            mock_ckpt.return_value.get.return_value = {
                "channel_values": {
                    "transcript": '[{"start": 0, "text": "test"}]',
                    "keyframes": [],
                    "keyframes_base_path": "",
                    "draft_summary": "",
                    "user_prompt": "",
                }
            }
            items = agent.answer_stream_with_context(
                question="当时发生了什么？",
                chat_id="task-1",
                owner_id="user-1",
                mode="timestamp",
                timestamp="00:05:00",
                window_seconds=20,
            )
            result = "".join(t for t in items if isinstance(t, str))
            assert "未配置" in result or "CHAT_API_KEY" in result


class TestSystemPromptTemplates:
    """Task 7.1: System prompt template tests."""

    def test_default_rag_prompt(self):
        agent = VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=MagicMock(),
            rag_stream_llm=MagicMock(),
        )
        prompt = agent._build_system_prompt(mode="rag")
        assert "视频内容问答助手" in prompt

    def test_default_timestamp_prompt(self):
        agent = VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=MagicMock(),
            rag_stream_llm=MagicMock(),
        )
        prompt = agent._build_system_prompt(mode="timestamp")
        assert "证据问答助手" in prompt

    def test_custom_template(self):
        agent = VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=MagicMock(),
            rag_stream_llm=MagicMock(),
            system_prompt_template="Custom: {mode}",
        )
        prompt = agent._build_system_prompt(mode="rag")
        assert prompt == "Custom: rag"


class TestAnswerStreamWithContext:
    """Task 7.1 & 7.2: Extended interface tests."""

    @pytest.fixture
    def agent(self):
        rag_svc = MagicMock()
        rag_svc._resolve_video_collection.return_value = "video_test123"
        rag_svc._build_retrieval_context.return_value = MagicMock(
            results=[MagicMock(text="Test chunk")],
            frames=[],
            cited_sources=[],
        )
        rag_svc._download_attachment_frames.return_value = []
        llm = MagicMock()
        llm._model_name = "test-model"
        llm._model.stream_chat_completion.return_value = iter(["ok"])
        return VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
        )

    def test_default_mode_is_rag(self, agent):
        tokens = list(agent.answer_stream_with_context(
            question="q", chat_id="t1", owner_id="u1",
        ))
        assert len(tokens) > 0

    def test_explicit_rag_mode(self, agent):
        tokens = list(agent.answer_stream_with_context(
            question="q", chat_id="t1", owner_id="u1", mode="rag",
        ))
        assert len(tokens) > 0

    def test_answer_stream_delegates_to_rag(self, agent):
        """BaseAgent.answer_stream() should use RAG mode by default."""
        tokens = list(agent.answer_stream(
            question="q", chat_id="t1", kbid="", owner_id="u1",
        ))
        assert len(tokens) > 0


class TestProgressEvents:
    """Verify AgentProgressEvent is yielded during streaming and filtered by answer()."""

    @pytest.fixture
    def agent(self):
        from core.agent.events import AgentProgressEvent
        rag_svc = MagicMock()
        rag_svc._resolve_video_collection.return_value = "video_test123"
        rag_svc._build_retrieval_context.return_value = MagicMock(
            results=[MagicMock(text="Test chunk"), MagicMock(text="Another chunk")],
            frames=[],
            cited_sources=[{"video_id": "v1", "quote": "Test chunk"}],
        )
        rag_svc._download_attachment_frames.return_value = []
        llm = MagicMock()
        llm._model_name = "test-model"
        llm._model.stream_chat_completion.return_value = iter(["回答token"])
        return VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
        )

    def test_answer_stream_yields_progress_events(self, agent):
        from core.agent.events import AgentProgressEvent
        items = list(agent.answer_stream(
            question="测试", chat_id="task-1", kbid="", owner_id="u1",
        ))
        progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
        assert len(progress_events) >= 2  # searching + generating
        phases = {e.phase for e in progress_events}
        assert "searching" in phases
        assert "generating" in phases

    def test_answer_filters_progress_events(self, agent):
        result = agent.answer(
            question="测试", chat_id="task-1", kbid="", owner_id="u1",
        )
        assert isinstance(result, str)
        assert "AgentProgressEvent" not in result

    def test_no_results_still_yields_progress(self, agent):
        from core.agent.events import AgentProgressEvent
        agent._rag_agent_service._build_retrieval_context.return_value = MagicMock(
            results=[],
            frames=[],
            cited_sources=[],
        )
        items = list(agent.answer_stream(
            question="无结果问题", chat_id="task-1", kbid="", owner_id="u1",
        ))
        progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
        # searching fires before retrieval; generating is skipped on early return
        assert len(progress_events) >= 1
        assert progress_events[0].phase == "searching"

    def test_timestamp_mode_yields_progress_events(self, agent):
        from core.agent.events import AgentProgressEvent
        with patch(
            "core.workflow.checkpoint_factory.create_checkpointer"
        ) as mock_ckpt:
            mock_ckpt.return_value.get.return_value = {
                "channel_values": {
                    "transcript": '[{"start": 0, "text": "test transcript content"}]',
                    "keyframes": [],
                    "keyframes_base_path": "",
                    "draft_summary": "draft",
                    "user_prompt": "",
                }
            }
            items = list(agent.answer_stream_with_context(
                question="当时发生了什么？",
                chat_id="task-1",
                owner_id="u1",
                mode="timestamp",
                timestamp="00:05:00",
                window_seconds=20,
            ))
            progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
            assert len(progress_events) >= 2  # loading + generating
            phases = {e.phase for e in progress_events}
            assert "loading" in phases
            assert "generating" in phases
