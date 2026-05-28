from typing import Any
from langgraph.graph import StateGraph, START, END
from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.nodes.chunk_state import ChunkState
from core.workflow.video_summary.planner.chunk_planner import chunk_planner_node
from core.workflow.video_summary.nodes.outline_bootstrap import outline_bootstrap_node
from core.workflow.video_summary.nodes.map_dispatcher import (
    map_dispatch_node,
    wave_gate_node,
    route_chunk_subgraph_tasks,
    route_after_wave_synthesis,
    ROUTE_CONTINUE_WAVE,
    ROUTE_WAVE_DONE,
)
from core.workflow.video_summary.nodes.chunk_audio_analyzer import chunk_audio_worker_node
from core.workflow.video_summary.nodes.chunk_vision_analyzer import chunk_vision_worker_node
from core.workflow.video_summary.nodes.chunk_aggregator import chunk_aggregator_node
from core.workflow.video_summary.nodes.human_gate import human_gate_node
from core.workflow.video_summary.nodes.fusion_drafter import fusion_drafter_node
from core.workflow.video_summary.nodes.data_preparation_node import data_preparation_node

# 质量审查节点
from core.workflow.video_summary.nodes.hallucination_grader import hallucination_grader_node
from core.workflow.video_summary.nodes.usefulness_grader import usefulness_grader_node

# 质量审查路由常量与路由函数
from core.workflow.video_summary.edges.router import (
    route_after_hallucination,
    route_after_usefulness,
    ROUTE_HAS_HALLUCINATION,
    ROUTE_NO_HALLUCINATION,
    ROUTE_NOT_USEFUL,
    ROUTE_USEFUL,
)


def build_chunk_subgraph() -> Any:
    """
    构建 chunk 子图：START → chunk_audio_worker_node → chunk_vision_worker_node → END。

    子图状态类型为 ChunkState，可独立编译用于单元测试。
    主图中使用 chunk_subgraph_node wrapper 函数驱动子图逻辑，
    以便将 ChunkState 结果正确映射回 VideoSummaryState.chunk_results。
    """
    subgraph = StateGraph(ChunkState)  # type: ignore
    subgraph.add_node("chunk_audio_worker_node", chunk_audio_worker_node)  # type: ignore
    subgraph.add_node("chunk_vision_worker_node", chunk_vision_worker_node)  # type: ignore
    subgraph.add_edge(START, "chunk_audio_worker_node")
    subgraph.add_edge("chunk_audio_worker_node", "chunk_vision_worker_node")
    subgraph.add_edge("chunk_vision_worker_node", END)
    return subgraph.compile()


def _make_chunk_subgraph_node():
    """
    返回 chunk_subgraph_node 包装函数。

    包装函数顺序执行 audio → vision 两步，并将 ChunkState 结果映射回
    VideoSummaryState.chunk_results（通过 _merge_chunk_results reducer 写回主图）。
    """
    def chunk_subgraph_node(state: dict) -> dict:
        chunk_id = str(state.get("chunk_id", "")).strip()
        if not chunk_id:
            return {"chunk_results": []}

        # Step 1: audio worker
        audio_delta = chunk_audio_worker_node(state)  # type: ignore
        chunk_state = {**state, **audio_delta}

        # Step 2: vision worker（读取 audio worker 产出的 transcript_claims）
        vision_delta = chunk_vision_worker_node(chunk_state)  # type: ignore
        chunk_state.update(vision_delta)

        result = {
            "chunk_id": chunk_id,
            "transcript_claims": chunk_state.get("transcript_claims", []),
            "frame_references": chunk_state.get("frame_references", []),
            "chunk_summary": chunk_state.get("chunk_summary", ""),
            "modality_status": chunk_state.get("modality_status", {}),
            "latency_ms": chunk_state.get("latency_ms", {}),
        }
        return {"chunk_results": [result]}

    return chunk_subgraph_node


def build_video_summary_graph(checkpointer: Any = None) -> Any:
    """
    构建视频总结工作流图（Phase 1）。

    新拓扑：
      START
        → chunk_planner_node
        → outline_bootstrap_node       (输出 narrative_arc)
        → data_preparation_node
        → map_dispatch_node
            → route_chunk_subgraph_tasks  [fan-out Send × N]
            → chunk_subgraph_node         (audio → vision 顺序，per chunk)
            → wave_gate_node              [fan-in，排序 + 调试信息]
            → route_after_wave_synthesis
                ├─ CONTINUE_WAVE → map_dispatch_node
                └─ WAVE_DONE    → chunk_aggregator_node
        → human_gate_node
        → END (pending_human_review)
    """
    workflow = StateGraph(VideoSummaryState)  # type: ignore

    # 注册节点
    workflow.add_node("chunk_planner_node", chunk_planner_node)  # type: ignore
    workflow.add_node("outline_bootstrap_node", outline_bootstrap_node)  # type: ignore
    workflow.add_node("data_preparation_node", data_preparation_node)  # type: ignore
    workflow.add_node("map_dispatch_node", map_dispatch_node)  # type: ignore
    workflow.add_node("chunk_subgraph_node", _make_chunk_subgraph_node())  # type: ignore
    workflow.add_node("wave_gate_node", wave_gate_node)  # type: ignore
    workflow.add_node("chunk_aggregator_node", chunk_aggregator_node)  # type: ignore
    workflow.add_node("human_gate_node", human_gate_node)  # type: ignore

    # 拓扑连线
    workflow.add_edge(START, "chunk_planner_node")
    workflow.add_edge("chunk_planner_node", "outline_bootstrap_node")
    workflow.add_edge("outline_bootstrap_node", "data_preparation_node")
    workflow.add_edge("data_preparation_node", "map_dispatch_node")

    # fan-out：map_dispatch_node → [chunk_subgraph_node × N]
    workflow.add_conditional_edges("map_dispatch_node", route_chunk_subgraph_tasks)

    # fan-in：每个 chunk_subgraph_node 完成后汇聚到 wave_gate_node
    workflow.add_edge("chunk_subgraph_node", "wave_gate_node")

    # 波次循环：wave_gate_node → route_after_wave_synthesis
    workflow.add_conditional_edges(
        "wave_gate_node",
        route_after_wave_synthesis,
        {
            ROUTE_CONTINUE_WAVE: "map_dispatch_node",
            ROUTE_WAVE_DONE: "chunk_aggregator_node",
        },
    )

    workflow.add_edge("chunk_aggregator_node", "human_gate_node")
    workflow.add_edge("human_gate_node", END)

    return workflow.compile(checkpointer=checkpointer)


def build_finalization_graph(checkpointer: Any = None) -> Any:
    """
    第二阶段（人类审批后）图：
    START -> fusion_drafter -> hallucination_grader -> usefulness_grader(循环) -> END
    """
    workflow = StateGraph(VideoSummaryState)  # type: ignore

    workflow.add_node("fusion_drafter_node", fusion_drafter_node)  # type: ignore
    workflow.add_node("hallucination_grader_node", hallucination_grader_node)  # type: ignore
    workflow.add_node("usefulness_grader_node", usefulness_grader_node)  # type: ignore

    workflow.add_edge(START, "fusion_drafter_node")
    workflow.add_edge("fusion_drafter_node", "hallucination_grader_node")
    workflow.add_conditional_edges(
        "hallucination_grader_node",
        route_after_hallucination,
        {
            ROUTE_HAS_HALLUCINATION: "fusion_drafter_node",
            ROUTE_NO_HALLUCINATION: "usefulness_grader_node",
        },
    )
    workflow.add_conditional_edges(
        "usefulness_grader_node",
        route_after_usefulness,
        {
            ROUTE_NOT_USEFUL: "fusion_drafter_node",
            ROUTE_USEFUL: END,
        },
    )

    return workflow.compile(checkpointer=checkpointer)