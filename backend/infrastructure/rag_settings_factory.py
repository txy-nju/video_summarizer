"""从 video_summarizer 的环境变量构造 MODULAR-RAG-MCP-SERVER 的 Settings 对象。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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

# 项目根目录：优先用环境变量 RAG_PROJECT_ROOT，其次从本文件位置推算，最后 fallback 到 os.getcwd()
_project_root = os.environ.get("RAG_PROJECT_ROOT")
if not _project_root:
    _project_root = str(Path(__file__).resolve().parent.parent.parent)
if not os.path.isdir(str(_project_root)):
    _project_root = os.getcwd()

_CHROMA_PERSIST_PATH = str(Path(_project_root) / "data" / "db" / "chroma")
_BM25_INDEX_DIR = str(Path(_project_root) / "data" / "db" / "bm25")
_TRACE_FILE = str(Path(_project_root) / "logs" / "traces.jsonl")

# 确保 BM25Indexer 在任意进程中都能找到同一份索引文件
if "BM25_INDEX_DIR" not in os.environ:
    os.environ["BM25_INDEX_DIR"] = _BM25_INDEX_DIR


def _resolve_bm25_index_path() -> str:
    """Resolve BM25 index file path, preferring env var if set."""
    custom = os.environ.get("BM25_INDEX_PATH")
    if custom:
        return custom
    return str(Path(_BM25_INDEX_DIR) / "bm25_index.json")


@lru_cache(maxsize=128)
def build_rag_settings(
    collection: str = "default",
    bm25_index_dir: str | None = None,
) -> Settings:
    """构造 RAG Settings 实例。

    Args:
        collection: Chroma 物理 collection 名称。视频 QA 使用 "default"，
                   KB QA 使用 KB 的 vector_collection_name（如 "kb_{uuid}"）。
        bm25_index_dir: Per-collection BM25 索引目录路径。None 表示使用全局
                       默认路径（BM25_INDEX_DIR 环境变量或 data/db/bm25）。

    persist_path / BM25 index 使用绝对路径从项目根目录推导，
    避免不同进程 CWD 不同导致访问不同的向量库文件。
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
    # Chroma collection 可参数化：KB QA 使用 per-KB 物理 collection 实现隔离；
    # 视频 QA 保持 "default" + metadata 过滤
    # persist_path 使用绝对路径，确保 Celery worker 和 Web 服务访问同一份 Chroma 数据
    vector_store = VectorStoreSettings(
        provider="chroma",
        collection=collection,
        persist_path=_CHROMA_PERSIST_PATH,
        bm25_index_dir=bm25_index_dir,
    )
    retrieval = RetrievalSettings(top_k=6)
    rerank = RerankSettings(provider="llm")
    evaluation = EvaluationSettings(backend="none")
    observability = ObservabilitySettings(
        log_level="INFO",
        trace_file=_TRACE_FILE,
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
