from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class RagAgentAnswer:
    answer_content: str
    cited_sources: list[dict]


class RagAgentService:
    """Local MCP-RAG adapter for global QA and video QA streaming."""

    def answer_global_question(
        self,
        *,
        owner_id: str,
        kbid: str,
        question_content: str,
        attachments: list[dict],
    ) -> RagAgentAnswer:
        answer = f"[RAG] 已基于知识库 {kbid} 检索并回答：{question_content}"
        cited_sources = [
            {
                "video_id": "global_kb",
                "task_id": None,
                "time_range": "00:00:00-00:00:00",
                "quote": question_content[:200],
                "score": 0.5,
            }
        ]
        if attachments:
            answer += f"（已参考 {len(attachments)} 个附件）"

        _ = owner_id
        return RagAgentAnswer(answer_content=answer, cited_sources=cited_sources)

    def stream_video_question(
        self,
        *,
        owner_id: str,
        task_id: str,
        question_content: str,
        attachments: list[dict],
    ) -> Iterator[str]:
        answer = f"[RAG] 已基于任务 {task_id} 检索并回答：{question_content}"
        if attachments:
            answer += f"（已参考 {len(attachments)} 个附件）"

        _ = owner_id
        for i in range(0, len(answer), 32):
            yield answer[i : i + 32]

    def stream_global_question(
        self,
        *,
        owner_id: str,
        kbid: str,
        question_content: str,
        attachments: list[dict],
    ) -> tuple[RagAgentAnswer, Iterator[str]]:
        """流式回答全局跨文档问题，返回 (完整答案元信息, 文本块迭代器)。"""
        answer = f"[RAG] 已基于知识库 {kbid} 检索并回答：{question_content}"
        cited_sources = [
            {
                "video_id": "global_kb",
                "task_id": None,
                "time_range": "00:00:00-00:00:00",
                "quote": question_content[:200],
                "score": 0.5,
            }
        ]
        if attachments:
            answer += f"（已参考 {len(attachments)} 个附件）"

        _ = owner_id
        full_answer = answer
        cited = cited_sources

        def _gen() -> Iterator[str]:
            for i in range(0, len(full_answer), 32):
                yield full_answer[i : i + 32]

        return RagAgentAnswer(answer_content=full_answer, cited_sources=cited), _gen()
