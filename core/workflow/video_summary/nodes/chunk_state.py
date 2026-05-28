from typing import TypedDict, List, Dict, Any, Optional


class ChunkState(TypedDict):
    """分片执行任务的状态契约。
    
    由 map_dispatcher 发送，由 chunk_multimodal_worker_node 执行。
    """
    # ---- 分片标识 ----
    chunk_id: str                           # 分片唯一标识，如 "chunk_0"

    # ---- 输入数据 ----
    transcript_segments: List[Dict]         # 当前分片对应的 transcript 段落列表
    keyframe_indexes: List[int]             # 当前分片在全局 keyframes 列表中的索引
    keyframes: List[Dict]                   # 当前分片对应的关键帧对象列表（含 time / frame_file 等）
    keyframes_base_path: str                # 关键帧文件引用根目录（透传自主图）

    # ---- 全局上下文（来自 outline_bootstrap_node） ----
    narrative_arc: Optional[List[Dict]]     # 全局叙事章节列表，结构: [{chapter_id, title, start_sec, end_sec, summary}]

    # ---- 滑动窗口上下文 ----
    previous_chunk_summaries: List[Dict[str, Any]]  # 最近 N 个分片的压缩摘要，用于 context_calibration

    # ---- worker 输出（chunk_multimodal_worker_node 写入） ----
    chunk_insights_md: str                  # 结构化 Markdown 输出，包含摘要与图文事件核验
    chunk_summary: str                      # 纯文本压缩摘要，用于 context_calibration（滑动窗口记忆）

    # ---- 可观测字段 ----
    modality_status: Dict[str, str]         # 各模态处理状态，如 {"multimodal": "ok"}
    latency_ms: Dict[str, Any]             # 各步骤耗时，结构与主图 chunk_results 内 latency_ms 一致
