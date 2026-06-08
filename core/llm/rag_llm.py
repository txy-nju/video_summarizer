"""RAG 专用流式 LLM 封装。

将底层 BaseModel 的 stream_chat_completion 接口适配为 RAG 问答所需的两种语义接口：
- stream_text：纯文本检索结果 + 问题 → token 流
- stream_multimodal：文本检索结果 + 帧图像 + 问题 → token 流（帧读取失败时自动降级）

通过 from_rag_settings(settings) 或 from_env() 工厂方法实例化，
调用方无需关心底层 provider、API key 等细节。
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Iterator

from core.llm.base import BaseModel

logger = logging.getLogger(__name__)

_NO_RESULTS_MSG = "未找到相关的视频内容，请确认视频已完成转录与向量化。"


class RagStreamLLM:
    """RAG 场景下的流式 LLM 统一接口。

    Parameters
    ----------
    model:
        已实例化的 BaseModel 实现（OpenAIModel / DeepSeekModel / QwenModel …）。
    model_name:
        传递给 API 的模型名称字符串（如 "gpt-4o"、"deepseek-chat"）。
    """

    def __init__(self, model: BaseModel, model_name: str) -> None:
        self._model = model
        self._model_name = model_name

    # ── 工厂方法 ──────────────────────────────────────────────────────

    @classmethod
    def from_rag_settings(cls, settings: Any) -> "RagStreamLLM":
        """从 modular_rag settings 对象构造。

        优先使用 settings.llm.{api_key, api_url, model} 字段直接创建
        OpenAIModel；若字段缺失则回退到 ``from_env()``（读环境变量）。
        """
        llm_cfg = getattr(settings, "llm", None)
        api_key = getattr(llm_cfg, "api_key", None)
        api_url = getattr(llm_cfg, "api_url", None)
        model_name = getattr(llm_cfg, "model", None)

        if api_key and model_name:
            from core.llm.openai_model import OpenAIModel

            return cls(
                model=OpenAIModel(api_key=api_key, base_url=api_url or None),
                model_name=model_name,
            )
        logger.debug("RagStreamLLM: rag settings 中未找到 api_key/model，回退到环境变量")
        return cls.from_env()

    @classmethod
    def from_env(cls) -> "RagStreamLLM":
        """从环境变量构造（使用 CHAT 能力配置）。"""
        from core.llm.factory import get_model_for_capability, get_model_name_for_capability

        return cls(
            model=get_model_for_capability("chat"),
            model_name=get_model_name_for_capability("chat"),
        )

    # ── 公开流式接口 ──────────────────────────────────────────────────

    def stream_text(
        self,
        *,
        question: str | None = None,
        results: list | None = None,
        messages: list[dict] | None = None,
        max_tokens: int = 1024,
    ) -> Iterator[str]:
        """纯文本 RAG 流式回答：将检索结果拼接为上下文，token 到达即 yield。

        支持两种调用方式：

        1. **便捷接口** (向后兼容): 传入 ``question`` + ``results``，
           内部自动构建单条 user message。
        2. **完整接口**: 传入 ``messages`` 列表（已包含 system prompt、
           历史消息和当前问题），直接调用 LLM。

        Parameters
        ----------
        question:
            用户问题（便捷接口）。
        results:
            HybridSearch / Reranker 返回的检索结果列表，每项须有 ``.text`` 属性（便捷接口）。
        messages:
            完整的 OpenAI-format messages 列表（完整接口）。
        max_tokens:
            最大生成 token 数，默认 1024。
        """
        if messages is not None:
            yield from self._model.stream_chat_completion(
                model=self._model_name,
                messages=messages,
                max_tokens=max_tokens,
            )
            return

        # 便捷接口：自行构建 messages
        if not results:
            yield _NO_RESULTS_MSG
            return

        text_context = "\n\n".join(r.text for r in results if r.text)
        messages = [
            {
                "role": "user",
                "content": (
                    f"请基于以下视频转录内容回答问题。\n\n"
                    f"转录内容：\n{text_context}\n\n问题：{question}"
                ),
            }
        ]
        yield from self._model.stream_chat_completion(
            model=self._model_name,
            messages=messages,
            max_tokens=max_tokens,
        )

    def stream_multimodal(
        self,
        *,
        question: str | None = None,
        results: list | None = None,
        frames: list[dict] | None = None,
        messages: list[dict] | None = None,
        max_tokens: int = 1024,
    ) -> Iterator[str]:
        """多模态 RAG 流式回答：文本检索结果 + 帧图像 + 问题 → token 流。

        支持两种调用方式：

        1. **便捷接口** (向后兼容): 传入 ``question`` + ``results`` + ``frames``。
        2. **完整接口**: 传入 ``messages`` 列表，直接调用 LLM（多模态内容
           需在 messages 中预构建）。

        Parameters
        ----------
        question:
            用户问题（便捷接口）。
        results:
            检索结果列表，每项须有 ``.text`` 属性（便捷接口）。
        frames:
            关键帧列表（便捷接口）。读取失败的帧会被跳过；若所有帧均失败，
            自动降级为 ``stream_text``。
        messages:
            完整的 OpenAI-format messages 列表（完整接口）。
        max_tokens:
            最大生成 token 数，默认 1024。
        """
        if messages is not None:
            yield from self._model.stream_chat_completion(
                model=self._model_name,
                messages=messages,
                max_tokens=max_tokens,
            )
            return

        # 便捷接口：自行构建多模态 messages
        text_context = "\n\n".join(r.text for r in results if r.text) if results else ""
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    f"请基于以下视频转录内容和对应视频帧回答问题。\n\n"
                    f"转录内容：\n{text_context}"
                ),
            }
        ]

        valid_frames = 0
        if frames:
            for f in frames:
                try:
                    with open(f["frame_path"], "rb") as img_file:
                        b64 = base64.b64encode(img_file.read()).decode()
                    label = f.get("time_range", "")
                    if label:
                        content.append({"type": "text", "text": f"视频帧（{label}）："})
                    mime = f.get("mime_type", "image/jpeg")
                    content.append(
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                    )
                    valid_frames += 1
                except OSError as exc:
                    logger.debug("stream_multimodal: 跳过帧 %s（%s）", f.get("frame_path"), exc)

        if valid_frames == 0:
            logger.warning("stream_multimodal: 所有帧读取失败，降级为纯文本模式")
            yield from self.stream_text(
                question=question, results=results, max_tokens=max_tokens
            )
            return

        content.append({"type": "text", "text": f"\n问题：{question}"})
        yield from self._model.stream_chat_completion(
            model=self._model_name,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
