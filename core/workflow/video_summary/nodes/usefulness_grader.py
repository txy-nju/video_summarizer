import json
from core.llm.base import BaseModel
from core.llm.factory import get_model_for_capability, get_model_name_for_capability
from core.workflow.video_summary.state import VideoSummaryState
from config.settings import SELF_RAG_MAX_REVISIONS as MAX_REVISIONS

def usefulness_grader_node(state: VideoSummaryState, llm_model: BaseModel | None = None) -> dict:
    """
    有用性审查节点。

    地位:
    - 位于 hallucination_grader_node 之后，是质量闭环的第二道防线。
    - 在事实基本成立的前提下，检查草稿是否真正回应了用户诉求（含第二阶段人类指导）。

    任务:
    - 对比 draft_summary 与 user_prompt/human_guidance。
    - 以 JSON Mode 返回是否满足用户需求。
    - 若偏题或遗漏重点，则输出定向修改指令并回流到成文节点。
    - 当无审核要求或达到重写上限时直接放行。

    主要输入:
    - state["draft_summary"]
    - state["user_prompt"]
    - state["human_guidance"]
    - state["revision_count"]

    主要输出:
    - usefulness_score: "yes" 或 "no"。
    - feedback_instructions: 供 fusion_drafter_node 使用的补充修改指令。

    :param state: VideoSummaryState
    :return: dict 更新 usefulness_score 和 feedback_instructions
    """
    draft = state.get("draft_summary", "")
    user_prompt = state.get("user_prompt", "")
    human_guidance = state.get("human_guidance", "")
    revision_count = state.get("revision_count", 0)
    aggregated_chunk_insights = state.get("aggregated_chunk_insights", "")

    # user_prompt 与 human_guidance 共同构成有用性评分的审核要求。
    review_requirements = []
    if isinstance(user_prompt, str) and user_prompt.strip():
        review_requirements.append(f"【用户原始总结侧重点】\n{user_prompt.strip()}")
    if isinstance(human_guidance, str) and human_guidance.strip():
        review_requirements.append(f"【人类审批补充指导】\n{human_guidance.strip()}")
    review_requirements_text = "\n\n".join(review_requirements)

    # 1. 熔断与防死循环
    # 如果草稿为空、达到重写上限，或没有任何审核要求，直接放行，避免无效调用。
    if not draft or revision_count >= MAX_REVISIONS or not review_requirements_text:
        return {"usefulness_score": "yes", "feedback_instructions": ""}

    # 凭证解析已下沉至 get_model_for_capability 工厂，此处不再手动检查 OPENAI_API_KEY。
    model_client = llm_model or get_model_for_capability("chat")

    # 2. 构造 System Prompt，要求强制 JSON 输出
    system_prompt = (
        "你是一名严格的用户体验评估官 (Usefulness Grader)。\n"
        "你的唯一任务是评估【总结草稿】是否充分、准确地回应了用户的【特定总结侧重点】。\n\n"
        "【核心约束 - 防止幻觉放大】：\n"
        "你提供的修改指令必须严格限定在【视频原始证据】范围内。\n"
        "- 你只能要求草稿调整对已有证据的侧重、排版或措辞表达。\n"
        "- 你绝对不能要求草稿添加视频原始证据中不存在的内容。\n"
        "- 若用户期望的内容在原始证据中确实不存在，score 应为 \"yes\"（即：证据边界内已尽力满足）并在 reason 中说明证据缺失，而不是要求凭空补充。\n\n"
        "【严格 JSON 格式输出要求】：\n"
        "你必须输出一个合法且格式化良好的 JSON 对象，包含以下两个字段：\n"
        "1. \"score\": 字符串，只能是 \"yes\"（草稿在证据范围内已充分满足用户需求）或 \"no\"（证据中存在相关内容但草稿未使用或偏离方向）。\n"
        "2. \"reason\": 字符串，如果 score 为 \"no\"，给出明确修改指令，且指令必须基于【视频原始证据】中实际存在的内容；如果 score 为 \"yes\"，置为空字符串 \"\"。"
    )

    evidence_boundary = str(aggregated_chunk_insights).strip() if aggregated_chunk_insights else ""
    evidence_section = (
        f"\n====== 视频原始证据边界 (Evidence Boundary) ======\n"
        f"{evidence_boundary}\n"
        f"================================================\n"
    ) if evidence_boundary else ""

    user_content = (
        f"【审核要求（原始需求 + 人类审批意见）】：\n{review_requirements_text}\n"
        f"{evidence_section}\n"
        f"【待评估的总结草稿】：\n{draft}"
    )

    print(f"  -> [Usefulness Grader] Checking if draft meets user prompt (Revision {revision_count})...")

    # 3. 执行评估 API 调用
    try:
        model_name = get_model_name_for_capability("chat")
        result_json_str = model_client.chat_completion(
            model=model_name, 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"}, # 开启 JSON Mode 获取确定性结果
            temperature=0.0, # 评估节点必须保持绝对客观冷静
        )

        result_json_str = result_json_str.strip()
        result = json.loads(result_json_str)
        
        # 兼容处理，默认放行
        score = result.get("score", "yes").lower()
        reason = result.get("reason", "")
        
        if score == "no":
            print("  -> [Usefulness Grader] Result: NO (Draft missed user intent). Routing back to Drafter.")
            feedback = f"【偏题拦截 - 需求未满足】：\n{reason}"
            return {"usefulness_score": "no", "feedback_instructions": feedback}
        else:
            print("  -> [Usefulness Grader] Result: YES (Draft is useful). Final Approval.")
            return {"usefulness_score": "yes", "feedback_instructions": ""}
            
    except Exception as e:
        # [增强可观察性] 异常降级兜底：记录日志并放行
        print(f"  -> [Usefulness Grader] Error or Invalid JSON: {str(e)}. Fallback to YES usefulness.")
        return {"usefulness_score": "yes", "feedback_instructions": ""}