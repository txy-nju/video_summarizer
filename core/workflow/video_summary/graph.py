from typing import Any
from langgraph.graph import StateGraph, START, END
from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.planner.chunk_planner import chunk_planner_node
from core.workflow.video_summary.nodes.outline_bootstrap import outline_bootstrap_node
from core.workflow.video_summary.nodes.map_dispatcher import (
    map_dispatch_node,
    synthesis_barrier_node,
    route_after_wave_synthesis,
    route_audio_send_tasks,
    route_synthesis_bypass_if_ready,
    route_synthesis_send_tasks,
    route_vision_send_tasks,
    ROUTE_CONTINUE_WAVE,
    ROUTE_WAVE_DONE,
)
from core.workflow.video_summary.nodes.chunk_audio_analyzer import chunk_audio_worker_node
from core.workflow.video_summary.nodes.chunk_vision_analyzer import chunk_vision_worker_node
from core.workflow.video_summary.nodes.chunk_synthesizer import chunk_synthesizer_node, chunk_synthesizer_worker_node
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


def build_video_summary_graph(checkpointer: Any = None) -> Any:
    """
    构建视频总结工作流图。
    主干流程为：分片规划 -> 音频/视觉分析 -> 分片融合 -> 聚合成文 -> 质量审查闭环。
    """
    # 1. 初始化 StateGraph，绑定状态结构
    workflow = StateGraph(VideoSummaryState) # type: ignore

    # 2. 注册节点
    workflow.add_node("chunk_planner_node", chunk_planner_node) # type: ignore
    workflow.add_node("outline_bootstrap_node", outline_bootstrap_node) # type: ignore
    workflow.add_node("data_preparation_node", data_preparation_node) # type: ignore
    workflow.add_node("map_dispatch_node", map_dispatch_node) # type: ignore
    workflow.add_node("synthesis_barrier_node", synthesis_barrier_node) # type: ignore
    workflow.add_node("chunk_audio_worker_node", chunk_audio_worker_node) # type: ignore
    workflow.add_node("chunk_vision_worker_node", chunk_vision_worker_node) # type: ignore
    workflow.add_node("chunk_synthesizer_worker_node", chunk_synthesizer_worker_node) # type: ignore
    workflow.add_node("chunk_synthesizer_node", chunk_synthesizer_node) # type: ignore
    workflow.add_node("chunk_aggregator_node", chunk_aggregator_node) # type: ignore
    workflow.add_node("human_gate_node", human_gate_node) # type: ignore

    # 3. 编排拓扑连线
    workflow.add_edge(START, "chunk_planner_node")
    workflow.add_edge("chunk_planner_node", "outline_bootstrap_node")
    workflow.add_edge("outline_bootstrap_node", "data_preparation_node")
    workflow.add_edge("data_preparation_node", "map_dispatch_node")

    workflow.add_conditional_edges("map_dispatch_node", route_audio_send_tasks)
    workflow.add_conditional_edges("map_dispatch_node", route_vision_send_tasks)
    # 防死锁旁路：audio+vision 均已就绪但 synthesis 未完成时，直接触发 synthesis_barrier_node
    workflow.add_conditional_edges("map_dispatch_node", route_synthesis_bypass_if_ready)

    # 先汇聚到 barrier，再触发 synthesis fan-out。
    workflow.add_edge("chunk_audio_worker_node", "synthesis_barrier_node")
    workflow.add_edge("chunk_vision_worker_node", "synthesis_barrier_node")
    workflow.add_conditional_edges("synthesis_barrier_node", route_synthesis_send_tasks)

    workflow.add_edge("chunk_synthesizer_worker_node", "chunk_synthesizer_node")

    # 第三阶段：按波次循环执行，直到所有 chunk 完成 synthesis。
    workflow.add_conditional_edges(
        "chunk_synthesizer_node",
        route_after_wave_synthesis,
        {
            ROUTE_CONTINUE_WAVE: "map_dispatch_node",
            ROUTE_WAVE_DONE: "chunk_aggregator_node",
        },
    )

    # 分片结果先聚合，再交由成文节点生成草稿
    workflow.add_edge("chunk_aggregator_node", "human_gate_node")
    # human_gate_node 始终以 pending 状态结束第一阶段，交由前端发起人类审批
    workflow.add_edge("human_gate_node", END)

    # 4. 编译并返回可执行工作流
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