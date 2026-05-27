from typing import Any, Dict, List, TypedDict, Annotated
from core.workflow.video_summary.state import _merge_chunk_results

class ChunkState(TypedDict):
    """
    单个分片的子图状态，专用于音视并发流水线 (Chunk Subgraph)。
    """
    # 来自主图的基础物料
    current_chunk: Dict[str, Any]
    transcript: str
    keyframes: List[Dict[str, Any]]
    keyframes_base_path: str
    user_prompt: str
    structured_global_context: Dict[str, Any]
    previous_chunk_summaries: List[Dict[str, Any]]
    trace_id: str
    
    # 子图流转状态
    audio_insights: List[Dict[str, Any]]
    
    # 最终输出结果，通过 Reducer 归入主图
    chunk_results: Annotated[List[Dict[str, Any]], _merge_chunk_results]
