"""Unit tests for MessageBuilder."""

from __future__ import annotations

import pytest

from core.context.message_builder import MessageBuilder


class TestMessageBuilder:
    """MessageBuilder message assembly and truncation."""

    @pytest.fixture
    def builder(self):
        return MessageBuilder()

    def test_build_simple_question_no_rag(self, builder):
        messages = builder.build(
            system_prompt="You are helpful.",
            history=[],
            rag_context="",
            current_question="Hello?",
        )
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1]["role"] == "user"
        assert "Hello?" in messages[1]["content"]

    def test_build_with_rag_context(self, builder):
        messages = builder.build(
            system_prompt="You are helpful.",
            history=[],
            rag_context="Transcript: AI is growing fast.",
            current_question="What about AI?",
        )
        assert len(messages) == 2
        assert "AI is growing fast" in messages[1]["content"]
        assert "What about AI?" in messages[1]["content"]

    def test_build_with_history(self, builder):
        history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        messages = builder.build(
            system_prompt="SYS",
            history=history,
            rag_context="",
            current_question="Q3",
        )
        # system + 4 history + 1 current = 6
        assert len(messages) == 6
        assert messages[0] == {"role": "system", "content": "SYS"}
        assert messages[1] == {"role": "user", "content": "Q1"}
        assert messages[2] == {"role": "assistant", "content": "A1"}
        assert messages[5]["role"] == "user"
        assert "Q3" in messages[5]["content"]

    def test_truncation_removes_earliest_pairs(self, builder):
        """When token budget is tight, earliest Q&A pairs are dropped."""
        history = [
            {"role": "user", "content": "x" * 500},    # ~250 tokens
            {"role": "assistant", "content": "y" * 500},  # ~250 tokens
            {"role": "user", "content": "z" * 100},     # ~50 tokens
            {"role": "assistant", "content": "w" * 100},  # ~50 tokens
        ]
        messages = builder.build(
            system_prompt="SYS",  # ~1 token
            history=history,
            rag_context="",
            current_question="Q",  # ~0.5 token
            max_tokens=200,  # Should force dropping the first pair
        )
        # Should have kept only the second pair
        assert len(messages) == 4  # system + 2 history + 1 current
        assert messages[1]["content"] == "z" * 100
        assert messages[2]["content"] == "w" * 100

    def test_truncation_with_sufficient_budget(self, builder):
        history = [
            {"role": "user", "content": "short"},
            {"role": "assistant", "content": "short"},
        ]
        messages = builder.build(
            system_prompt="SYS",
            history=history,
            rag_context="",
            current_question="Q",
            max_tokens=10000,  # huge budget
        )
        assert len(messages) == 4

    def test_estimate_tokens(self, builder):
        assert builder._estimate_tokens("") == 0
        assert builder._estimate_tokens("hello") == 2  # 5 // 2

    def test_empty_history_is_handled(self, builder):
        messages = builder.build(
            system_prompt="SYS",
            history=None,
            rag_context="",
            current_question="Q",
        )
        assert len(messages) == 2

    def test_current_user_message_without_rag(self, builder):
        msg = builder._build_current_user_message(rag_context="", question="Q")
        assert msg["role"] == "user"
        assert "Q" in msg["content"]
        assert "转录内容" not in msg["content"]

    def test_current_user_message_with_rag(self, builder):
        msg = builder._build_current_user_message(rag_context="RAG text", question="Q")
        assert "转录内容" in msg["content"]
        assert "RAG text" in msg["content"]
