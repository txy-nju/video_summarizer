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
        # Phase 1: decision (non-streaming)
        llm._model.chat_completion.return_value = "DECISION: search"
        # Phase 2: stream answer
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
        # Phase 1: decision
        llm._model.chat_completion.return_value = "DECISION: search"
        # Phase 2: stream
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
        # Phase 1: decision
        llm._model.chat_completion.return_value = "DECISION: search"
        # Phase 2: stream
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
        assert len(progress_events) >= 2  # deciding + searching + generating
        phases = {e.phase for e in progress_events}
        assert "deciding" in phases
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
        # deciding fires first, then searching, then early return on no results
        assert len(progress_events) >= 1
        phases = {e.phase for e in progress_events}
        assert "deciding" in phases

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


class TestTwoPhaseRAGMode:
    """Tests for the two-phase decision + streaming answer in RAG mode."""

    @pytest.fixture
    def agent_with_decision(self):
        """Agent with decision phase enabled and mock LLM for both phases."""
        rag_svc = MagicMock()
        rag_svc._resolve_video_collection.return_value = "video_test123"
        rag_svc._build_retrieval_context.return_value = MagicMock(
            results=[MagicMock(text="Test chunk")],
            frames=[],
            cited_sources=[{"video_id": "v1", "quote": "Test chunk"}],
        )
        rag_svc._download_attachment_frames.return_value = []
        llm = MagicMock()
        llm._model_name = "test-model"
        llm._model.chat_completion.return_value = "DECISION: search"
        llm._model.stream_chat_completion.return_value = iter(["回答token"])
        return VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
            enable_decision_phase=True,
        ), rag_svc, llm

    def test_answer_stream_yields_tokens_incrementally(self, agent_with_decision):
        """Task 6.2: Phase 2 tokens are yielded one-by-one, not buffered."""
        agent, _, _ = agent_with_decision
        items = list(agent.answer_stream(
            question="测试问题", chat_id="task-1", kbid="", owner_id="u1",
        ))
        text_tokens = [t for t in items if isinstance(t, str)]
        # Each token from the stream iterator should appear as a separate str
        assert len(text_tokens) == 1
        assert text_tokens[0] == "回答token"

    def test_decision_answer_skips_rag(self, agent_with_decision):
        """Task 6.3: DECISION: answer skips RAG retrieval."""
        agent, rag_svc, llm = agent_with_decision
        llm._model.chat_completion.return_value = "DECISION: answer"
        llm._model.stream_chat_completion.return_value = iter(["直接回答"])

        items = list(agent.answer_stream(
            question="刚才说的那个视频叫什么？", chat_id="task-1", kbid="", owner_id="u1",
        ))

        # RAG retrieval should NOT be called
        rag_svc._build_retrieval_context.assert_not_called()
        # Should yield deciding + generating + answer tokens
        text = "".join(t for t in items if isinstance(t, str))
        assert "直接回答" in text

    def test_decision_search_triggers_rag(self, agent_with_decision):
        """Task 6.4: DECISION: search triggers RAG retrieval."""
        agent, rag_svc, _ = agent_with_decision

        list(agent.answer_stream(
            question="这个视频里关于机器学习有什么讨论？", chat_id="task-1", kbid="", owner_id="u1",
        ))

        # RAG retrieval SHOULD be called
        rag_svc._build_retrieval_context.assert_called_once()

    def test_decision_fallback_on_unknown_format(self, agent_with_decision):
        """Task 6.5: Unknown decision format fallback → proceed to search."""
        agent, rag_svc, llm = agent_with_decision
        # LLM returns garbled text that doesn't match DECISION format
        llm._model.chat_completion.return_value = "嗯，这个问题需要查一下..."

        list(agent.answer_stream(
            question="测试问题", chat_id="task-1", kbid="", owner_id="u1",
        ))

        # Should fall through to RAG retrieval (safer to search)
        rag_svc._build_retrieval_context.assert_called_once()

    def test_progress_events_sequence(self, agent_with_decision):
        """Task 6.7: Progress events follow correct sequence.

        Expected: deciding → searching → generating → (tokens)
        """
        from core.agent.events import AgentProgressEvent
        agent, _, _ = agent_with_decision

        items = list(agent.answer_stream(
            question="测试", chat_id="task-1", kbid="", owner_id="u1",
        ))
        progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
        phases = [e.phase for e in progress_events]

        # Verify order: deciding → searching → generating
        assert phases[0] == "deciding"
        assert phases[1] == "searching"
        assert phases[-1] == "generating"  # generating is last progress event
        assert "generating" in phases

    def test_skip_decision_phase_when_disabled(self):
        """When enable_decision_phase=False, RAG mode skips deciding event."""
        from core.agent.events import AgentProgressEvent
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

        agent = VideoQAAgent(
            memory=_FakeChatMemory(),
            rag_agent_service=rag_svc,
            rag_stream_llm=llm,
            enable_decision_phase=False,
        )

        items = list(agent.answer_stream(
            question="测试", chat_id="task-1", kbid="", owner_id="u1",
        ))
        progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
        phases = {e.phase for e in progress_events}
        # No deciding event when decision phase is disabled
        assert "deciding" not in phases
        assert "searching" in phases
        assert "generating" in phases


class TestQAAgentTwoPhase:
    """Tests for the two-phase QAAgent (Global QA)."""

    @pytest.fixture
    def qa_agent_with_mocks(self):
        """Create a QAAgent with mocked dependencies for two-phase testing."""
        from core.agent.qa_agent import QAAgent

        memory = _FakeChatMemory()
        tool_registry = MagicMock()
        tool_registry.list_for_llm.return_value = "rag_search: Search the knowledge base"
        tool_executor = MagicMock()
        tool_executor.execute.return_value = MagicMock(
            success=True,
            data="检索结果: 测试内容",
            cited_sources=[{"video_id": "v1", "quote": "test"}],
        )

        llm = MagicMock()
        llm._model_name = "test-model"
        # First call: "search", second call: "answer" (realistic two-phase flow)
        llm._model.chat_completion.side_effect = ["DECISION: search", "DECISION: answer"]
        llm._model.stream_chat_completion.return_value = iter(["这是", "流式", "回答"])

        agent = QAAgent(
            memory=memory,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            rag_stream_llm=llm,
            max_iterations=2,
        )
        return agent, memory, tool_executor, llm

    def test_answer_stream_yields_tokens_incrementally(self, qa_agent_with_mocks):
        """Task 6.2: Phase 2 tokens are yielded individually, not as one blob."""
        from core.agent.events import AgentProgressEvent
        agent, _, _, _ = qa_agent_with_mocks

        items = list(agent.answer_stream(
            question="测试问题", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))
        text_tokens = [t for t in items if isinstance(t, str)]
        # Three tokens from the mock stream iterator
        assert len(text_tokens) == 3
        assert text_tokens == ["这是", "流式", "回答"]

    def test_decision_answer_skips_tool_execution(self, qa_agent_with_mocks):
        """Task 6.3: DECISION: answer skips tool execution."""
        agent, _, tool_executor, llm = qa_agent_with_mocks
        llm._model.chat_completion.side_effect = ["DECISION: answer"]
        llm._model.stream_chat_completion.return_value = iter(["直接回答"])

        list(agent.answer_stream(
            question="刚才说的那个叫什么？", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))

        # Tool executor should NOT be called
        tool_executor.execute.assert_not_called()

    def test_decision_search_executes_tool(self, qa_agent_with_mocks):
        """Task 6.4: DECISION: search triggers tool execution."""
        agent, _, tool_executor, _ = qa_agent_with_mocks

        list(agent.answer_stream(
            question="新知识点问题", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))

        # Tool executor SHOULD be called with rag_search
        tool_executor.execute.assert_called_once()
        call_kwargs = tool_executor.execute.call_args.kwargs
        assert call_kwargs["tool_name"] == "rag_search"

    def test_decision_fallback_on_unknown(self, qa_agent_with_mocks):
        """Task 6.5: Unknown decision → fail-open (treat as answer)."""
        agent, _, tool_executor, llm = qa_agent_with_mocks
        llm._model.chat_completion.side_effect = ["让我想想..."]

        list(agent.answer_stream(
            question="测试", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))

        # Tool executor should NOT be called (fallback to answer)
        tool_executor.execute.assert_not_called()

    def test_max_iterations_forces_answer(self, qa_agent_with_mocks):
        """Task 6.6: Max iterations reached → force answer phase."""
        agent, _, tool_executor, llm = qa_agent_with_mocks
        # Always return "search", so agent loops until max_iterations (2)
        llm._model.chat_completion.side_effect = ["DECISION: search", "DECISION: search"]
        llm._model.stream_chat_completion.return_value = iter(["最终回答"])

        items = list(agent.answer_stream(
            question="反复检索的问题", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))

        # max_iterations=2, so tool should be called 2 times then forced answer
        assert tool_executor.execute.call_count == 2
        text = "".join(t for t in items if isinstance(t, str))
        assert "最终回答" in text

    def test_progress_events_sequence(self, qa_agent_with_mocks):
        """Task 6.7: Progress events follow correct sequence."""
        from core.agent.events import AgentProgressEvent
        agent, _, _, _ = qa_agent_with_mocks

        items = list(agent.answer_stream(
            question="测试", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))
        progress_events = [i for i in items if isinstance(i, AgentProgressEvent)]
        phases = [e.phase for e in progress_events]

        # Sequence: deciding → searching → retrieved → generating
        assert phases[0] == "deciding"
        assert "searching" in phases
        assert "retrieved" in phases
        assert phases[-1] == "generating"

    def test_answer_filters_progress_events(self, qa_agent_with_mocks):
        """Non-streaming answer() filters progress events."""
        agent, _, _, _ = qa_agent_with_mocks

        result = agent.answer(
            question="测试", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        )
        assert isinstance(result, str)
        assert "这是流式回答" in result
        assert "AgentProgressEvent" not in result

    def test_last_cited_sources_from_tool_execution(self, qa_agent_with_mocks):
        """last_cited_sources comes from RAG tool execution, not parser."""
        agent, _, _, _ = qa_agent_with_mocks

        list(agent.answer_stream(
            question="测试", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))

        assert len(agent.last_cited_sources) == 1
        assert agent.last_cited_sources[0]["video_id"] == "v1"

    def test_decision_llm_error_fallback(self, qa_agent_with_mocks):
        """When decision LLM call fails, error message is yielded."""
        agent, _, _, llm = qa_agent_with_mocks
        llm._model.chat_completion.side_effect = Exception("API error")

        items = list(agent.answer_stream(
            question="测试", chat_id="chat-1", kbid="kb-1", owner_id="u1",
        ))
        text = "".join(t for t in items if isinstance(t, str))
        assert "失败" in text or "API error" in text
