"""Unit tests for ReAct output parser."""

from __future__ import annotations

import pytest

from core.agent.parser import ParsedOutput, parse_react_output


class TestParseReactOutput:
    """Parser behaviour."""

    def test_parses_final_answer(self):
        text = "FINAL_ANSWER: 这是一个测试回答。"
        result = parse_react_output(text)
        assert result.final_answer == "这是一个测试回答。"
        assert result.action is None
        assert not result.is_action

    def test_parses_thought_before_final_answer(self):
        text = "THOUGHT: 我需要回答问题。\nFINAL_ANSWER: 答案在这里。"
        result = parse_react_output(text)
        assert result.thought == "我需要回答问题。"
        assert result.final_answer == "答案在这里。"

    def test_parses_action_rag_search(self):
        text = "THOUGHT: 需要检索\nACTION: rag_search\nQUERY: 机器学习"
        result = parse_react_output(text)
        assert result.is_action
        assert result.action == "rag_search"
        assert result.action_params == {"query": "机器学习"}
        assert result.final_answer is None

    def test_parses_action_without_thought(self):
        text = "ACTION: rag_search\nQUERY: 深度学习"
        result = parse_react_output(text)
        assert result.is_action
        assert result.action == "rag_search"
        assert result.action_params == {"query": "深度学习"}

    def test_parses_chinese_colon(self):
        text = "THOUGHT： 中文冒号\nFINAL_ANSWER： 中文答案"
        result = parse_react_output(text)
        assert result.thought == "中文冒号"
        assert result.final_answer == "中文答案"

    def test_fallback_when_no_markers(self):
        text = "这是没有标记的自由文本回答。"
        result = parse_react_output(text)
        assert result.final_answer == "这是没有标记的自由文本回答。"
        assert result.action is None
        assert not result.is_action

    def test_fallback_empty_text(self):
        result = parse_react_output("")
        assert result.final_answer is not None
        assert "未返回" in result.final_answer

    def test_fallback_none_text(self):
        result = parse_react_output(None)
        assert result.final_answer is not None
        assert "未返回" in result.final_answer

    def test_parses_multiline_thought(self):
        text = (
            "THOUGHT: 第一行思考\n第二行思考\n第三行\n"
            "FINAL_ANSWER: 多行思考后的回答。"
        )
        result = parse_react_output(text)
        assert "第一行思考" in result.thought
        assert result.final_answer == "多行思考后的回答。"

    def test_parses_multiline_final_answer(self):
        text = "FINAL_ANSWER: 第一行\n第二行\n第三行"
        result = parse_react_output(text)
        assert "第一行" in result.final_answer
        assert "第二行" in result.final_answer

    def test_final_answer_takes_priority_over_action(self):
        """When both ACTION and FINAL_ANSWER exist, the parser picks FINAL_ANSWER."""
        text = "ACTION: rag_search\nQUERY: test\nFINAL_ANSWER: 直接回答"
        result = parse_react_output(text)
        assert result.final_answer is not None
        assert result.action is None

    def test_unknown_action_falls_back_to_text(self):
        text = "ACTION: unknown_tool\nPARAM: value"
        result = parse_react_output(text)
        assert result.final_answer == text
        assert result.action is None

    def test_parsed_output_is_action_property(self):
        action_out = ParsedOutput(action="rag_search", action_params={"query": "x"})
        assert action_out.is_action

        answer_out = ParsedOutput(final_answer="hello")
        assert not answer_out.is_action


class TestParsedOutputDataclass:
    """ParsedOutput data class behaviour."""

    def test_defaults(self):
        p = ParsedOutput()
        assert p.thought == ""
        assert p.action is None
        assert p.action_params == {}
        assert p.final_answer is None
        assert not p.is_action

    def test_action_requires_both_action_and_no_answer(self):
        p = ParsedOutput(action="rag_search", action_params={"query": "q"})
        assert p.is_action

    def test_final_answer_suppresses_action_flag(self):
        p = ParsedOutput(action="rag_search", final_answer="直接回答")
        assert not p.is_action
