import json
from core.llm.base import BaseModel
from core.llm.factory import get_model_for_capability, get_model_name_for_capability
from core.workflow.video_summary.state import VideoSummaryState
from config.settings import SELF_RAG_MAX_REVISIONS as MAX_REVISIONS

def hallucination_grader_node(state: VideoSummaryState, llm_model: BaseModel | None = None) -> dict:
    """
    幻觉审查节点。

    地位:
    - 位于 fusion_drafter_node 之后，是质量闭环的第一道防线。
    - 负责判断草稿是否超出了上游证据范围，决定是否回到成文节点重写。

    任务:
    - 对比 draft_summary 与 aggregated_chunk_insights。
    - 以 JSON Mode 返回是否存在 hallucination。
    - 若发现问题，则输出可直接用于重写的 feedback_instructions。
    - 在达到 MAX_REVISIONS 或草稿为空时主动熔断放行，避免死循环。

    主要输入:
    - state["draft_summary"]
    - state["aggregated_chunk_insights"]
    - state["revision_count"]

    主要输出:
    - hallucination_score: "yes" 或 "no"。
    - feedback_instructions: 供 fusion_drafter_node 使用的纠错指令。
    
    :param state: VideoSummaryState
    :return: dict 更新 hallucination_score 和 feedback_instructions
    """
    draft = state.get("draft_summary", "")
    aggregated_chunk_insights = state.get("aggregated_chunk_insights", "")
    revision_count = state.get("revision_count", 0)
    structured_global_context = state.get("structured_global_context") or {}

    # 1. 熔断防死循环
    if not draft or revision_count >= MAX_REVISIONS:
        return {"hallucination_score": "no", "feedback_instructions": ""}

    # 凭证解析已下沉至 get_model_for_capability 工厂，此处不再手动检查 OPENAI_API_KEY。
    model_client = llm_model or get_model_for_capability("chat")

    # 2. 构造极其严苛的 System Prompt，要求强制 JSON 输出
    system_prompt = (
        "你是一名无情、极其严苛的【时空幻觉核查员 (Hallucination Grader)】。\n"
        "你的唯一任务是对【待审草稿】进行像素级的事实核查。你必须对比【源数据】（听觉与视觉提取结果），判断草稿中是否存在任何源数据中未提及的捏造信息（如编造的百分比、数字、没发生过的动作、未提及的技术概念）。\n\n"
        "【证据层级与使用规则】：\n"
        "- 一级证据（最终裁决依据）：聚合分片证据（aggregated_chunk_insights），你必须以此为准。\n"
        "- 二级约束（辅助核查信号）：结构化全局上下文（structured_global_context），包含从原始转录中提取的实体列表与时间锚点。\n"
        "  使用规则：\n"
        "  1. 若草稿出现某实体，且该实体在一级证据与实体列表中均无任何支撑，则判为可疑幻觉。\n"
        "  2. 若草稿时间描述与时间锚点的 start_sec/end_sec 明显矛盾，可作为定位依据。\n"
        "  3. 不可仅因某实体不在实体列表就判为幻觉，必须同时确认一级证据也缺失或冲突。\n\n"
        "【严格 JSON 格式输出要求】：\n"
        "你必须输出一个合法且格式化良好的 JSON 对象，包含以下三个字段：\n"
        "1. \"score\": 字符串，只能是 \"yes\"（说明草稿中**存在幻觉捏造**）或 \"no\"（说明没有幻觉，草稿事实均有根据）。\n"
        "2. \"faulty_timestamp\": 字符串，如果发现幻觉，请精确指出虚假信息在草稿或时间轴中出现的大致位置（如 '14:20' 或 '第二段'）；如果没有幻觉，置为空字符串 \"\"。\n"
        "3. \"reason\": 字符串，如果存在幻觉，请给出极其精确的切除指令（例如：'请立即删掉关于 React 状态管理的解读，因为画面和语音只展示了最终效果，纯属大模型脑补'）；如果没有幻觉，置为空字符串 \"\"。"
    )

    evidence_sources = str(aggregated_chunk_insights).strip()

    # 格式化二级约束块（仅在 structured_global_context 非空时附加）
    outline_section = ""
    if structured_global_context:
        entities = structured_global_context.get("entities") or []
        timeline_anchors = structured_global_context.get("timeline_anchors") or []
        if entities or timeline_anchors:
            entity_names = ", ".join(
                str(e.get("name", "")).strip()
                for e in entities
                if isinstance(e, dict) and e.get("name")
            )
            anchor_lines = "; ".join(
                f"{a.get('chunk_id', '')}[{a.get('start_sec', 0)}s-{a.get('end_sec', 0)}s]"
                for a in timeline_anchors
                if isinstance(a, dict)
            )
            parts = []
            if entity_names:
                parts.append(f"已知实体：{entity_names}")
            if anchor_lines:
                parts.append(f"时间锚点：{anchor_lines}")
            outline_section = (
                f"\n====== 二级约束：结构化全局上下文 (Secondary Constraint) ======\n"
                + "\n".join(parts)
                + "\n================================================================"
            )

    user_content = (
        f"====== 事实核查源数据 (Truth Sources) ======\n"
        f"{evidence_sources}\n"
        f"========================================\n"
        f"{outline_section}\n\n"
        f"【待严格审查的总结草稿】：\n{draft}"
    )

    print(f"  -> [Hallucination Grader] Checking for hallucinations (Revision {revision_count})...")

    # 3. 执行评估 API 调用
    try:
        model_name = get_model_name_for_capability("chat")
        result_json_str = model_client.chat_completion(
            model=model_name, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"}, # [核心创新] 开启 JSON Mode，获取确定性判决
            temperature=0.0, # 必须为 0，防止核查员自己产生幻觉
        )

        result_json_str = result_json_str.strip()
        result = json.loads(result_json_str)
        
        score = result.get("score", "no").lower()
        reason = result.get("reason", "")
        faulty_timestamp = result.get("faulty_timestamp", "")
        
        if score == "yes":
            print(f"  -> [Hallucination Grader] Result: YES (Hallucination detected at {faulty_timestamp}). Routing back to Drafter.")
            feedback = f"【幻觉拦截 - 发生位置 {faulty_timestamp}】：\n{reason}"
            return {"hallucination_score": "yes", "feedback_instructions": feedback}
        else:
            print("  -> [Hallucination Grader] Result: NO (Factually grounded). Proceeding to Usefulness Check.")
            return {"hallucination_score": "no", "feedback_instructions": ""}
            
    except Exception as e:
        # [增强可观察性] 当异常降级发生时，必须记录日志以供调试追溯
        print(f"  -> [Hallucination Grader] Error or Invalid JSON: {str(e)}. Fallback to NO hallucination.")
        # 异常兜底，防止 JSON 解析失败或网络异常卡死状态机
        return {"hallucination_score": "no", "feedback_instructions": ""}