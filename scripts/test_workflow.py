import os
import json
import logging
from pprint import pprint
import sys

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.workflow.video_summary.graph import build_video_summary_graph, build_finalization_graph
from core.workflow.video_summary.state import VideoSummaryState

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def get_mock_transcript() -> str:
    mock_data = {
        "chunks": [
            {
                "text": "大家好，欢迎来到本期视频。今天我们深入体验一下最新的GPT-4o的多模态能力。它的语音延迟极低，简直像真人在交流。",
                "timestamp": [0.0, 15.0]
            },
            {
                "text": "现在我们看看画面，我展示了一段代码，它不仅能读懂代码，还能发现其中的安全漏洞，我们来看一下具体演示过程。",
                "timestamp": [15.0, 30.0]
            }
        ]
    }
    return json.dumps(mock_data, ensure_ascii=False)

def get_mock_keyframes() -> list:
    return [
        {"time": 5.0, "frame_file": "frame_5.jpg", "image": "mock_base64"},
        {"time": 20.0, "frame_file": "frame_20.jpg", "image": "mock_base64"},
    ]

def main():
    print("==========================================")
    print("      Starting Workflow Integration Test  ")
    print("==========================================\n")

    # 1. Initialize State
    initial_state = VideoSummaryState(
        trace_id="test-trace-123",
        transcript=get_mock_transcript(),
        keyframes=get_mock_keyframes(),
        keyframes_base_path="",
        user_prompt="侧重于多模态能力的分析",
        structured_global_context={},
        data_preparation_status={},
        data_preparation_events=[],
        aggregated_chunk_insights="",
        human_edited_aggregated_insights="",
        human_guidance="",
        human_gate_status="",
        human_gate_reason="",
        video_duration_seconds=0,
        chunk_plan=[],
        chunk_results=[],
        chunk_summary_memory={},
        previous_chunk_summaries_by_chunk={},
        active_wave_chunk_ids=[],
        wave_index=0,
        current_chunk={},
        current_synthesis_chunk={},
        current_synthesis_base_item={},
        chunk_audio_insights={},
        chunk_visual_insights={},
        chunk_retry_count={},
        reduce_debug_info={},
        draft_summary="",
        hallucination_score="",
        usefulness_score="",
        feedback_instructions="",
        revision_count=0,
    )

    # 2. Build and Run Phase 1 Graph (Video Summary)
    print("==> 阶段1: 运行视频总结主干图 (Planning -> Subgraph -> Aggregation)")
    app = build_video_summary_graph()
    
    # run phase 1
    phase1_state = app.invoke(initial_state)

    print("\n--- [输出] 全局大纲 (Narrative Arc) ---")
    narrative_arc = phase1_state.get("structured_global_context", {}).get("narrative_arc", [])
    pprint(narrative_arc)

    print("\n--- [输出] 分批子图结果 (Chunk Results) ---")
    chunk_results = phase1_state.get("chunk_results", [])
    for item in chunk_results:
        print(f"\nChunk ID: {item.get('chunk_id')}")
        print(f"Verified Insights: ")
        pprint(item.get("verified_insights", []))
        print(f"Modality Status: {item.get('modality_status', {})}")

    print("\n--- [输出] 聚合稿 (Aggregated Insights) ---")
    print(phase1_state.get("aggregated_chunk_insights", ""))

    print("\n==> 阶段2: 模拟人类审批 (Mock Human Gate)")
    # 3. Mock Human Approval
    phase1_state["human_gate_status"] = "approved"
    phase1_state["human_guidance"] = "生成的草稿很好，请按原计划成文。"
    print(f"人类状态: {phase1_state['human_gate_status']}")
    print(f"人类指导: {phase1_state['human_guidance']}")

    print("\n==> 阶段3: 运行终稿图 (Fusion Drafter -> Quality Graders)")
    # 4. Build and Run Phase 2 Graph (Finalization)
    final_app = build_finalization_graph()
    
    # run phase 2
    final_state = final_app.invoke(phase1_state)

    print("\n--- [输出] 最终输出草稿 (Draft Summary) ---")
    print(final_state.get("draft_summary", ""))
    print("\n--- [输出] 审查得分 ---")
    print(f"幻觉分数: {final_state.get('hallucination_score', '')}")
    print(f"实用性分数: {final_state.get('usefulness_score', '')}")
    print(f"重写次数: {final_state.get('revision_count', 0)}")
    
    print("\n==========================================")
    print("      Workflow Integration Test Complete  ")
    print("==========================================")

if __name__ == "__main__":
    main()
