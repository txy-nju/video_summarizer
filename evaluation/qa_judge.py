"""
LLM-as-a-Judge 时间旅行追问评估模块。

对 video_summarizer 的 ask_at_timestamp 能力进行自动化离线评估：
- 答案相关性（是否切题）
- 答案准确性（事实是否匹配参考答案）
- 答案完整性（是否覆盖关键点）
- 时间精度（是否聚焦正确的视频时间窗口）
"""

from typing import Any, Dict, List, Optional

from evaluation.llm_as_a_judge import (
    _chat_json,
    _get_client,
    _get_model_name,
    _label_from_score,
    _safe_int,
)

# ── 维度权重 ──────────────────────────────────────────
QA_RELEVANCE_WEIGHT = 0.30
QA_ACCURACY_WEIGHT = 0.35
QA_COMPLETENESS_WEIGHT = 0.20
QA_TEMPORAL_WEIGHT = 0.15

# ── 阈值复用（与 llm_as_a_judge 保持一致） ────────────
QA_PASS_THRESHOLD = 0.75
QA_WARN_THRESHOLD = 0.55


def _label_from_score_qa(score: Optional[float]) -> str:
    """QA 专用标签（阈值逻辑与主 Judge 一致，但 label 前缀加 qa- 以示区分）。"""
    if score is None:
        return "na"
    if score >= QA_PASS_THRESHOLD:
        return "qa-pass"
    if score >= QA_WARN_THRESHOLD:
        return "qa-warn"
    return "qa-fail"


def evaluate_qa_answer(
    generated_answer: str,
    question: str,
    timestamp: str,
    reference_answer: str,
    reference_key_points: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """评估单条时间旅行追问的回答质量。

    Args:
        generated_answer: 系统生成的回答文本。
        question: 用户提出的问题。
        timestamp: 用户指向的视频时间戳（如 "5:30"）。
        reference_answer: 人工参考答案。
        reference_key_points: 参考答案中的关键要点列表（可选）。
        api_key: OpenAI API key。
        base_url: OpenAI base URL。
        model_name: 法官模型名（优先级高于环境变量）。

    Returns:
        一个包含 score / label / dimensions / details 的 dict。
    """
    if not generated_answer.strip():
        return {
            "score": 0.0,
            "label": "qa-fail",
            "details": "generated answer is empty",
            "dimensions": {
                "relevance": 0,
                "accuracy": 0,
                "completeness": 0,
                "temporal_precision": 0,
            },
            "judge_confidence": 0,
            "judge_reasoning": "",
        }

    if not question.strip():
        return {
            "score": None,
            "label": "na",
            "details": "question is empty",
            "dimensions": {},
            "judge_confidence": 0,
            "judge_reasoning": "",
        }

    key_points_text = ""
    if reference_key_points:
        key_points_text = "\n".join(f"- {kp}" for kp in reference_key_points)

    client = _get_client(api_key=api_key, base_url=base_url)
    resolved_model = _get_model_name(model_name)

    system_prompt = (
        "你是一名严格的时间旅行追问质量裁判。"
        "AI 系统通过时间窗机制工作：它只能看到视频指定时间戳 (timestamp) 前后各约 20 秒"
        "的语音转录文本和关键帧图像，基于这个狭窄的时间窗口内的证据来回答问题。"
        "你需要评估该 AI 回答的质量。"
        "请从以下四个维度分别打分（每个维度 0-100 整数）：\n\n"
        "1. relevance (相关性)：回答是否直接回应了用户问题？有没有答非所问、兜圈子？\n"
        "2. accuracy (准确性)：与参考答案相比，回答中的事实是否准确？有没有凭空编造？\n"
        "3. completeness (完整性)：是否覆盖了参考答案中的关键要点？有没有重要遗漏？\n"
        "4. temporal_precision (时间精度)：回答是否聚焦在指定时间戳附近的内容？\n"
        "   如果回答明显引用了视频其他时间段的内容（而非 target 时间窗内的内容），"
        "此维度应打低分。如果系统主动说明「该时间窗证据不足」也算合理。\n\n"
        "同时给出：\n"
        "- overall_score: 0-100 整数综合评分\n"
        "- confidence: 0-100 整数，表示你对此评分的信心\n"
        "- reasoning: 简短中文评语，说明主要优点和不足\n\n"
        "输出 JSON 对象。"
    )

    user_input_parts = [
        f"[question]\n{question}",
        f"[timestamp]\n{timestamp}",
        f"[generated_answer]\n{generated_answer}",
    ]
    if reference_answer.strip():
        user_input_parts.append(f"[reference_answer]\n{reference_answer}")
    if key_points_text:
        user_input_parts.append(f"[reference_key_points]\n{key_points_text}")

    user_prompt = "\n\n".join(user_input_parts)

    try:
        payload = _chat_json(client, system_prompt, user_prompt, model_name=resolved_model)
    except Exception:
        return {
            "score": None,
            "label": "na",
            "details": "judge LLM call failed",
            "dimensions": {},
            "judge_confidence": 0,
            "judge_reasoning": "",
        }

    # 解析维度分数
    relevance = min(max(_safe_int(payload.get("relevance", 0), 0), 0), 100)
    accuracy = min(max(_safe_int(payload.get("accuracy", 0), 0), 0), 100)
    completeness = min(max(_safe_int(payload.get("completeness", 0), 0), 0), 100)
    temporal_precision = min(max(_safe_int(payload.get("temporal_precision", 0), 0), 0), 100)

    overall_raw = payload.get("overall_score")
    if overall_raw is not None:
        overall = min(max(_safe_int(overall_raw, 50), 0), 100)
    else:
        # 如果法官没给 overall，用加权计算
        overall = int(
            QA_RELEVANCE_WEIGHT * relevance
            + QA_ACCURACY_WEIGHT * accuracy
            + QA_COMPLETENESS_WEIGHT * completeness
            + QA_TEMPORAL_WEIGHT * temporal_precision
        )

    confidence = min(max(_safe_int(payload.get("confidence", 70), 70), 0), 100)
    reasoning = str(payload.get("reasoning", "")).strip()

    score = round(overall / 100.0, 4)
    label = _label_from_score_qa(score)

    return {
        "score": score,
        "label": label,
        "details": "",
        "dimensions": {
            "relevance": relevance,
            "accuracy": accuracy,
            "completeness": completeness,
            "temporal_precision": temporal_precision,
        },
        "overall_raw": overall,
        "judge_confidence": confidence,
        "judge_reasoning": reasoning,
    }
