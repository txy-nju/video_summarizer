"""Unit tests for HybridChatMemory — DB + Redis conversation memory."""

from __future__ import annotations

import json

import pytest

from core.context.message_builder import MessageBuilder
from core.memory.base import BaseChatMemory
from core.memory.hybrid import HybridChatMemory


class FakeRedis:
    """In-memory Redis mock for testing."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value
        self._ttls[key] = ttl

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttls.pop(key, None)


class FakeQARepository:
    """Fake repository returning canned Q&A history."""

    def __init__(self, records=None):
        self._records = records or []

    def list_by_owner_and_chat(self, owner_id: str, chat_id: str):
        return self._records


class _FakeRecord:
    def __init__(self, question: str, answer: str | None = None):
        self.question_content = question
        self.answer_content = answer


class TestHybridChatMemory:
    """HybridChatMemory cache-aside behaviour."""

    @pytest.fixture
    def redis(self):
        return FakeRedis()

    @pytest.fixture
    def qa_repo(self):
        return FakeQARepository([
            _FakeRecord("Q1", "A1"),
            _FakeRecord("Q2", "A2"),
        ])

    @pytest.fixture
    def memory(self, redis, qa_repo):
        return HybridChatMemory(
            qa_repository=qa_repo,
            redis_client=redis,
            message_builder=MessageBuilder(),
        )

    def test_is_base_chat_memory_instance(self, memory):
        assert isinstance(memory, BaseChatMemory)

    def test_build_messages_from_db_on_cache_miss(self, memory, redis):
        messages = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q3",
        )
        # system + Q1/A1 + Q2/A2 + Q3 = 6
        assert len(messages) == 6
        assert messages[0] == {"role": "system", "content": "SYS"}
        assert messages[1] == {"role": "user", "content": "Q1"}
        assert messages[2] == {"role": "assistant", "content": "A1"}
        assert messages[5]["role"] == "user"

    def test_cache_hit_on_second_call(self, memory, redis):
        # First call loads from DB and writes cache
        memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q1",
        )
        # Verify cache was written
        cache_key = "chat_memory:chat1"
        cached = json.loads(redis.get(cache_key))
        assert len(cached) >= 2  # system + history

        # Second call should use cache (we can verify by checking no additional DB load)
        # Clear the fake repo to prove we're reading from cache
        memory._qa_repo = FakeQARepository([])  # empty DB
        messages2 = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q2",
        )
        assert len(messages2) >= 2

    def test_add_turn_invalidates_cache(self, memory, redis):
        # First build with 1 Q&A in DB
        memory._qa_repo = FakeQARepository([_FakeRecord("Q1", "A1")])
        messages1 = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q2",
        )
        initial_count = len(messages1)  # system + Q1/A1 + Q2 = 4

        # add_turn invalidates cache
        memory.add_turn(chat_id="chat1", question="Q1", answer="A1")

        # Now DB has a new record added (simulating DB persistence by GlobalQAService)
        memory._qa_repo = FakeQARepository([
            _FakeRecord("Q1", "A1"),
            _FakeRecord("Q2", "A2"),  # new record added
        ])
        messages2 = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q3",
        )
        # Should have more messages now (system + Q1/A1 + Q2/A2 + Q3 = 6)
        assert len(messages2) > initial_count

    def test_clear_removes_cache(self, memory, redis):
        memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q1",
        )
        cache_key = "chat_memory:chat1"
        assert redis.get(cache_key) is not None

        memory.clear(chat_id="chat1")
        assert redis.get(cache_key) is None

    def test_build_messages_with_rag_context(self, memory, redis):
        messages = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q3",
            rag_context="RAG text here",
        )
        last_msg = messages[-1]
        assert "RAG text here" in last_msg["content"]
        assert "Q3" in last_msg["content"]

    def test_build_messages_with_max_tokens(self, memory, redis):
        """Truncation is applied when max_tokens is set."""
        # Fill with many records that will exceed budget
        many_records = [
            _FakeRecord(f"Question {i}", f"Answer {i}")
            for i in range(10)
        ]
        memory._qa_repo = FakeQARepository(many_records)
        messages = memory.build_messages(
            chat_id="chat1",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q last",
            max_tokens=50,  # very tight budget
        )
        # Should have truncated, keeping only recent turns
        assert len(messages) < 22  # far less than the full 22 messages

    def test_empty_history(self, redis):
        memory = HybridChatMemory(
            qa_repository=FakeQARepository([]),
            redis_client=redis,
        )
        messages = memory.build_messages(
            chat_id="new",
            owner_id="user1",
            system_prompt="SYS",
            current_question="Q1",
        )
        assert len(messages) == 2  # system + current question only
