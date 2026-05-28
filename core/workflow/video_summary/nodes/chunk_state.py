from typing import TypedDict, List, Dict, Any, Optional


class ChunkState(TypedDict):
    """子图（audio → vision 顺序流水线）的内部状态契约。

    该 TypedDict 仅在 chunk_subgraph 内部流转，不向 VideoSummaryState 直接暴露。
    子图执行结束后，相关字段通过 chunk_results reducer 写回主图状态。
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

    # ---- audio worker 输出（chunk_audio_analyzer 写入） ----
    transcript_claims: List[Dict]           # 来自 transcript 的原子断言，结构: [{claim, exact_quote, timestamp}]

    # ---- vision worker 输出（chunk_vision_analyzer 写入） ----
    frame_references: List[Dict]            # 画面观察与音频断言交叉验证，结构: [{frame_time, observation, audio_claim_match}]
    chunk_summary: str                      # 融合音频事实 + 画面验证的叙事摘要（vision worker 产出，聚合器降级兜底用）

    # ---- 可观测字段 ----
    modality_status: Dict[str, str]         # 各模态处理状态，如 {"audio": "done", "vision": "done"}
    latency_ms: Dict[str, Any]             # 各步骤耗时，结构与主图 chunk_results 内 latency_ms 一致
