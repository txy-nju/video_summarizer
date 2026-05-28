from typing import TypedDict, List, Dict, Any, Annotated, Optional


def _deep_merge_dict(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    """
    递归深度合并字典：
    - 同名键且双方为 dict：继续递归合并
    - 其他类型：update 覆盖 base
    """
    merged = dict(base)
    for key, value in update.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged

def _merge_chunk_results(base: List[Dict], update: List[Dict]) -> List[Dict]:
    """
    深度合并两个并行分支的 chunk_results，确保来自不同节点的更新不会相互覆盖。
    
    策略：
    1. 按 chunk_id 构建索引，使用 base 中的原始顺序
    2. chunk 级对象使用递归深度合并（适配增量差异写入）
    3. 保持 chunk_results 列表的顺序稳定
    
    Args:
        base: 第一个并行分支返回的 chunk_results（如 audio worker 聚合结果）
        update: 第二个并行分支返回的 chunk_results（如 vision worker 聚合结果）
    
    Returns:
        已合并的 chunk_results，包含来自两个分支的所有更新
    """
    if not base:
        return update if isinstance(update, list) else []
    if not update:
        return base if isinstance(base, list) else []
    
    def _extract_chunk_id(item: Dict[str, Any]) -> str:
        """从字典项中安全提取 chunk_id，返回空字符串表示无效"""
        chunk_id = item.get("chunk_id")
        # 避免 None 被转为字符串 "None"
        if chunk_id is None or not isinstance(chunk_id, str):
            return ""
        return chunk_id.strip()
    
    # 构建结果映射（chunk_id → merged_item）
    result_map: Dict[str, Dict[str, Any]] = {}
    
    # 阶段 1: 加入 base 中的所有项
    for item in base:
        if isinstance(item, dict):
            chunk_id = _extract_chunk_id(item)
            if chunk_id:
                result_map[chunk_id] = dict(item)  # 浅拷贝
    
    # 阶段 2: 合并 update 中的项
    for item in update:
        if isinstance(item, dict):
            chunk_id = _extract_chunk_id(item)
            if chunk_id:
                if chunk_id in result_map:
                    existing = result_map[chunk_id]
                    result_map[chunk_id] = _deep_merge_dict(existing, item)
                else:
                    # 新的 chunk_id，直接加入
                    result_map[chunk_id] = dict(item)
    
    # 阶段 3: 按原始 base 顺序重新排序，并追加新增项
    ordered: List[Dict[str, Any]] = []
    seen: set = set()
    
    for base_item in base:
        if isinstance(base_item, dict):
            chunk_id = _extract_chunk_id(base_item)
            if chunk_id and chunk_id in result_map:
                ordered.append(result_map[chunk_id])
                seen.add(chunk_id)
    
    # 追加只在 update 中的新项（保持 update 的顺序）
    for update_item in update:
        if isinstance(update_item, dict):
            chunk_id = _extract_chunk_id(update_item)
            if chunk_id and chunk_id not in seen and chunk_id in result_map:
                ordered.append(result_map[chunk_id])
                seen.add(chunk_id)
    
    return ordered


class VideoSummaryState(TypedDict):
    trace_id: str                   # 写入: Workflow API；消费: 节点 span 与 LLM tracing
    # 输入层数据
    transcript: str                 # 语音转录结果，通常为 Whisper verbose_json 字符串
    keyframes: List[Dict]           # 关键帧列表，元素至少包含 time，可能包含 image 或 frame_file
    keyframes_base_path: str        # 关键帧文件引用模式下的根目录
    user_prompt: str                # 用户具体的总结侧重点
    narrative_arc: Optional[List[Dict]]  # 写入: outline_bootstrap_node；消费: map worker / chunk workers；结构: [{chapter_id, title, start_sec, end_sec, summary}]
    data_preparation_status: Dict[str, Any]  # 写入: data_preparation_node；消费: 状态透传/可观测
    data_preparation_events: List[Dict[str, Any]]  # 写入: data_preparation_node；消费: 状态服务与前端诊断
    
    # 中间态数据
    aggregated_chunk_insights: str  # 写入: chunk_aggregator_node；消费: fusion_drafter_node / hallucination_grader_node
    human_edited_aggregated_insights: str  # 写入: human_gate_node / finalize API；消费: fusion_drafter_node
    human_guidance: str  # 写入: human_gate_node / finalize API；消费: fusion_drafter_node
    human_gate_status: str  # 写入: human_gate_node / finalize API；消费: route_after_human_gate
    human_gate_reason: str  # 写入: human_gate_node；消费: 前端审批态展示

    # 分片执行中间态
    video_duration_seconds: int     # 写入: chunk_planner_node；消费: 主要用于观测和测试，当前主链路不直接依赖
    chunk_plan: List[Dict]          # 写入: chunk_planner_node；消费: map_dispatch_node / worker 路由函数 / chunk_subgraph_node / chunk_aggregator_node
    chunk_results: Annotated[List[Dict], _merge_chunk_results]  # 写入: chunk_audio_worker / chunk_vision_worker（子图）；消费: wave_gate_node / chunk_aggregator_node / 进度上报逻辑
    chunk_summary_memory: Dict[str, str]  # 写入: map_dispatch_node；消费: worker context_calibration 轻量上下文
    previous_chunk_summaries_by_chunk: Dict[str, List[Dict[str, Any]]]  # 写入: map_dispatch_node；消费: route_audio_send_tasks / route_vision_send_tasks
    active_wave_chunk_ids: List[str]  # 写入: map_dispatch_node；消费: audio/vision/synthesis 路由与 barrier
    wave_index: int                # 写入: map_dispatch_node；消费: 调度诊断、前端可观测
    current_chunk: Dict             # 写入: route_audio_send_tasks / route_vision_send_tasks；消费: chunk_audio_worker_node / chunk_vision_worker_node
    chunk_audio_insights: Dict      # 预留字段；当前主链路未稳定写入，未来可用于按 chunk_id 建立音频侧映射缓存
    chunk_visual_insights: Dict     # 预留字段；当前主链路未稳定写入，未来可用于按 chunk_id 建立视觉侧映射缓存
    chunk_retry_count: Dict         # 写入: map_dispatch_node；消费: 当前主链路主要用于状态透传和未来重试策略扩展
    reduce_debug_info: Dict         # 写入: map_dispatch_node / wave_gate_node / chunk_aggregator_node；消费: 前端调试展示、测试断言、运行诊断
    
    # 输出与循环控制
    draft_summary: str              # 写入: fusion_drafter_node；消费: hallucination_grader_node / usefulness_grader_node / finalize_summary() 返回
    
    # 质量审查与重写控制
    hallucination_score: str        # 写入: hallucination_grader_node；消费: route_after_hallucination / 前端告警文案
    usefulness_score: str           # 写入: usefulness_grader_node；消费: route_after_usefulness / 前端告警文案
    feedback_instructions: str      # 写入: hallucination_grader_node / usefulness_grader_node；消费: fusion_drafter_node

    revision_count: int             # 写入: fusion_drafter_node；消费: hallucination_grader_node / usefulness_grader_node / 前端状态文案