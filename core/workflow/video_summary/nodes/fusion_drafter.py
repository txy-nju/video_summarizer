from core.llm.base import BaseModel
from core.llm.factory import get_model_for_capability, get_model_name_for_capability
from core.workflow.video_summary.state import VideoSummaryState
from backend.observability.llm_tracing import trace_llm_call
from backend.observability.tracing import build_span_name, start_span

def fusion_drafter_node(state: VideoSummaryState, llm_model: BaseModel | None = None) -> dict:
    """
    全局成文节点。

    地位:
    - 位于分片聚合之后，是生成最终 draft_summary 的核心节点。
    - 同时也是质量闭环中的“被打回重写”节点。

    任务:
    - 读取 aggregated_chunk_insights 并生成完整 Markdown 总结。
    - 结合 user_prompt 控制总结重点。
    - 若 feedback_instructions 非空，则按审查意见进行定向重写。

    主要输入:
    - state["aggregated_chunk_insights"]
    - state["user_prompt"]
    - state["feedback_instructions"]
    - state["revision_count"]

    主要输出:
    - draft_summary: 当前轮次生成的总结草稿。
    - revision_count: 重写轮次递增后的值。
    
    :param state: VideoSummaryState
    :return: dict 包含更新的 draft_summary 和增加的 revision_count
    """
    current_count = state.get("revision_count", 0)
    aggregated_chunk_insights = state.get("aggregated_chunk_insights", "")
    human_edited_aggregated_insights = state.get("human_edited_aggregated_insights", "")
    human_guidance = state.get("human_guidance", "")
    user_prompt = state.get("user_prompt", "")
    feedback_instructions = state.get("feedback_instructions", "")
    trace_id = str(state.get("trace_id", ""))

    # 凭证解析已下沉至 get_model_for_capability 工厂，此处不再手动检查 OPENAI_API_KEY。
    model_client = llm_model or get_model_for_capability("chat")

    # 2. 构造 System Prompt（聚合输入 -> 最终成文）
    system_prompt = (
        "你是一个顶级的视频内容编辑与深度报告撰写专家。\n"
        "你的输入是按时间轴和章节大纲聚合后的结构化多模态事实列表（Chunk Aggregated Insights）。\n"
        "你的核心任务是：基于这些极其干瘪的事实骨架，发挥你的语言天赋，撰写一篇语义连贯、逻辑通顺、具有深度洞察的正式文章。\n\n"
        "【架构级约束指令】：\n"
        "1. 🔗 绝对忠于底层事实：你必须并且只能使用输入事实清单中的信息，严禁根据你的预训练知识捏造任何未在输入中出现过的动作、观点或因果关系。\n"
        "2. 🧭 结构自洽：请遵循输入材料中提供的章节标题大纲结构（## 标题名）来进行内容组织。\n"
        "3. 📍 溯源锚点机制：在你生成的每一段或每一句描述特定事实的句子末尾，必须保留并打上对应的时间戳锚点（如 `[02:15]`）。\n"
        "4. 📝 专业脚注要求：如果你在事实列表中看到生僻的专业技术术语（或者缩写），并且该术语并未在上下文中解释，请基于你自身的丰富知识库，在整篇文章的末尾自动生成一个【名词解释附录】，解释这些术语的背景含义。\n"
        "5. 🎨 优美的过渡：输入的事实是碎片化的，请使用具有连接性的过渡句将它们巧妙地编织在一起，确保读者获得流畅的阅读体验。"
    )

    if human_guidance and str(human_guidance).strip():
        system_prompt += (
            "\n\n【人类审批补充指令（最高优先级）】：\n"
            f"{human_guidance}\n"
            "请在不违背证据约束的前提下，严格遵从以上人类指令。"
        )

    # 若上游审查节点给出了反馈，则在本轮生成中附带修改约束
    if feedback_instructions and feedback_instructions.strip():
        system_prompt += (
            f"\n\n⚠️ 【重要警告：这是第 {current_count + 1} 次重写草稿】\n"
            "在上一版的草稿中，双重质量评分器 (Grader) 指出了以下的幻觉捏造或偏题问题。请务必在本次生成中严格遵照以下修改指令进行定点切除与修正：\n"
            f"====== 强制修改指令 (Feedback Instructions) ======\n"
            f"{feedback_instructions}\n"
            f"=================================================="
        )

    # 4. 组装 User Content
    effective_aggregated_insights = (
        human_edited_aggregated_insights
        if str(human_edited_aggregated_insights).strip()
        else aggregated_chunk_insights
    )

    user_content = (
        f"【用户期望的总结侧重点】：\n{user_prompt}\n\n"
        f"【分片聚合证据（唯一输入）】：\n{effective_aggregated_insights}"
    )

    print(f"  -> [Fusion Drafter Node] Drafting final report from aggregated chunk insights (Revision {current_count + 1})...")

    # 5. 执行 API 调用
    with start_span(
        build_span_name("workflow", "finalization", "draft"),
        attributes={
            "trace_id": trace_id,
            "scope": "workflow_finalization",
            "scope_id": "fusion_drafter",
            "workflow_state": "FINAL_GENERATING",
        },
    ):
        try:
            model_name = get_model_name_for_capability("chat")
            with trace_llm_call(
                provider="openai_compatible",
                model=model_name,
                scope="fusion_drafter",
                scope_id="final_summary",
                workflow_state="FINAL_GENERATING",
            ):
                draft = model_client.chat_completion(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    # 融合节点需要将碎片化信息组织为流畅文章，因此适度提高 temperature 以获取更好的文笔和行文组织能力
                    temperature=0.5,
                )
            print("  -> [Fusion Drafter Node] Draft synthesized successfully.")

            return {
                "draft_summary": draft,
                "revision_count": current_count + 1
            }
        except Exception as e:
            print(f"  -> [Fusion Drafter Node] Error during synthesis: {str(e)}")
            # 将异常上抛，由后续路由或最终结果展现
            return {
                "draft_summary": f"[系统自动提示]：综合图文大纲失败，LLM 调用发生异常：{str(e)}",
                "revision_count": current_count + 1,
                "error_code": "FUSION_DRAFTER_FAILED",
                "status": "ERROR",
            }