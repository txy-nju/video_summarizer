"""从 video_summarizer 的环境变量构造 MODULAR-RAG-MCP-SERVER 的 Settings 对象。"""
from __future__ import annotations

import os
from functools import lru_cache

from modular_rag.core.settings import (
    Settings,
    LLMSettings,
    EmbeddingSettings,
    SplitterSettings,
    VectorStoreSettings,
    RetrievalSettings,
    RerankSettings,
    EvaluationSettings,
    ObservabilitySettings,
    IngestionSettings,
)


@lru_cache(maxsize=1)
def build_rag_settings() -> Settings:
    """构造 RAG Settings 实例。

    Chroma collection 名称固定为 'default'；
    查询/摄取时的数据域隔离通过 chunk 元数据字段 collection 实现，而非切换 Chroma collection。
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL") or None
    model_name = os.environ.get("OPENAI_MODEL_NAME", "gpt-4o")
    embedding_model = os.environ.get("RAG_EMBEDDING_MODEL", "text-embedding-3-small")

    llm = LLMSettings(
        provider="openai",
        model=model_name,
        api_key=api_key,
        api_url=base_url,
    )
    embedding = EmbeddingSettings(
        provider="openai",
        model=embedding_model,
        api_key=api_key,
        api_url=base_url,
    )
    splitter = SplitterSettings(
        provider="recursive",
        chunk_size=512,
        chunk_overlap=64,
    )
    # Chroma collection 名称固定；分类隔离由 metadata.collection 过滤实现
    vector_store = VectorStoreSettings(
        provider="chroma",
        collection="default",
        persist_path="data/db/chroma",
    )
    retrieval = RetrievalSettings(top_k=6)
    rerank = RerankSettings(provider="llm")
    evaluation = EvaluationSettings(backend="none")
    observability = ObservabilitySettings(
        log_level="INFO",
        trace_file="logs/traces.jsonl",
    )
    # 转录文本无需 LLM 精炼、元数据增强或图片描述，全部关闭以节省 Token
    ingestion = IngestionSettings()

    return Settings(
        llm=llm,
        embedding=embedding,
        splitter=splitter,
        vector_store=vector_store,
        retrieval=retrieval,
        rerank=rerank,
        evaluation=evaluation,
        observability=observability,
        ingestion=ingestion,
    )
