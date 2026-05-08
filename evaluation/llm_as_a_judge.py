import json
import os
from copy import deepcopy
from threading import Lock
from typing import Any, Dict, List, Optional

from openai import OpenAI

ENTITY_HALLUCINATION_PENALTY = 0.2
RELATION_HALLUCINATION_PENALTY = 0.5
FABRICATION_HALLUCINATION_PENALTY = 1.0

FACT_SUPPORT_WEIGHT = 0.6
FACT_ANTI_HALLUCINATION_WEIGHT = 0.25
FACT_CONFIDENCE_WEIGHT = 0.15
FACT_WEIGHTED_PENALTY_CAP = 0.35

TASK_COVERAGE_WEIGHT = 0.85
TASK_CONFIDENCE_WEIGHT = 0.15
TASK_PARTIAL_CREDIT_WEIGHT = 0.7

PASS_THRESHOLD = 0.75
WARN_THRESHOLD = 0.55
REVIEW_MIN_THRESHOLD = 0.5
REVIEW_MAX_THRESHOLD = 0.65

_TASK_REQUIREMENTS_CACHE: Dict[tuple[str, str, Optional[str], str], List[Dict[str, Any]]] = {}
_TASK_REQUIREMENTS_CACHE_LOCK = Lock()


def _label_from_score(score: Optional[float]) -> str:
    if score is None:
        return "na"
    if score >= PASS_THRESHOLD:
        return "pass"
    if score >= WARN_THRESHOLD:
        return "warn"
    if REVIEW_MIN_THRESHOLD <= score <= REVIEW_MAX_THRESHOLD:
        return "review"
    return "fail"


def _get_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> OpenAI:
    resolved_api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    resolved_base_url = (base_url or os.getenv("OPENAI_BASE_URL", "")).strip() or None
    if not resolved_api_key:
        raise ValueError("OPENAI_API_KEY is required for llm-as-a-judge evaluation")
    return OpenAI(api_key=resolved_api_key, base_url=resolved_base_url)


def _get_model_name(model_name: Optional[str] = None) -> str:
    return (model_name or os.getenv("OPENAI_MODEL_NAME", "gpt-4o")).strip() or "gpt-4o"


def _chat_json(
    client: OpenAI,
    system_prompt: str,
    user_prompt: str,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    response = client.chat.completions.create(
        model=_get_model_name(model_name),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    content = response.choices[0].message.content or "{}"
    return json.loads(content)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_importance(value: Any) -> int:
    return min(max(_safe_int(value, 3), 1), 5)


def _sum_importance(rows: List[Dict[str, Any]]) -> int:
    return sum(_normalize_importance(row.get("importance", 3)) for row in rows)


def _normalize_claims(raw_claims: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_claims, list):
        return []

    claims: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_claims, start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("claim_text", "")).strip()
        if not text:
            continue
        claims.append(
            {
                "claim_id": str(item.get("claim_id", f"claim-{idx:03d}")).strip() or f"claim-{idx:03d}",
                "claim_text": text,
                "claim_type": str(item.get("claim_type", "fact")).strip() or "fact",
                "importance": _normalize_importance(item.get("importance", 3)),
            }
        )
    return claims


def _normalize_requirements(raw_requirements: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_requirements, list):
        return []

    requirements: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_requirements, start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("requirement_text", "")).strip()
        if not text:
            continue
        requirements.append(
            {
                "requirement_id": str(item.get("requirement_id", f"req-{idx:03d}")).strip() or f"req-{idx:03d}",
                "requirement_text": text,
                "importance": _normalize_importance(item.get("importance", 3)),
            }
        )
    return requirements


def extract_claims(
    generated_summary: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not generated_summary.strip():
        return []

    client = _get_client(api_key=api_key, base_url=base_url)
    system_prompt = (
        "你是一名严格的事实声明抽取器。"
        "请从总结文本中抽取所有可验证的独立事实声明。"
        "不要抽取纯主观感受、修辞句、空泛评价。"
        "每条 claim 必须能被单独验证。"
        "输出 JSON 对象，字段 claims 为数组。"
        "每个元素必须包含 claim_id、claim_text、claim_type、importance。"
        "importance 取 1 到 5。"
    )
    user_prompt = f"请从下面总结中抽取独立事实声明。\n\n[generated_summary]\n{generated_summary}"
    payload = _chat_json(client, system_prompt, user_prompt, model_name=model_name)
    return _normalize_claims(payload.get("claims", []))


def verify_claims(
    claims: List[Dict[str, Any]],
    reference_evidence: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not claims or not reference_evidence.strip():
        return []

    client = _get_client(api_key=api_key, base_url=base_url)
    system_prompt = (
        "你是一名声明级事实核查裁判。"
        "你必须只依据参考资料判断每条 claim。"
        "对每条 claim 输出以下字段：claim_id、status、hallucination_type、confidence、evidence、reason。"
        "status 只能是 supported、contradicted、not_mentioned。"
        "hallucination_type 只能是 none、entity、relation_action、fabrication。"
        "规则："
        "如果参考资料明确支持，status=supported 且 hallucination_type=none。"
        "如果 claim 与参考资料冲突，优先标注 contradicted，并根据错误类型选择 entity 或 relation_action。"
        "如果参考资料完全没有提及该 claim，标注 not_mentioned；若属于凭空捏造的重要事实，hallucination_type=fabrication。"
        "confidence 取 0 到 100 的整数。"
        "输出 JSON 对象，字段 verifications 为数组。"
    )
    user_prompt = (
        f"[reference_evidence]\n{reference_evidence}\n\n"
        f"[claims]\n{json.dumps(claims, ensure_ascii=False, indent=2)}"
    )
    payload = _chat_json(client, system_prompt, user_prompt, model_name=model_name)
    raw_rows = payload.get("verifications", [])
    if not isinstance(raw_rows, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for claim in claims:
        claim_id = claim["claim_id"]
        matched = None
        for row in raw_rows:
            if isinstance(row, dict) and str(row.get("claim_id", "")).strip() == claim_id:
                matched = row
                break
        if matched is None:
            matched = {}
        status = str(matched.get("status", "not_mentioned")).strip().lower()
        if status not in {"supported", "contradicted", "not_mentioned"}:
            status = "not_mentioned"
        hallucination_type = str(matched.get("hallucination_type", "none")).strip().lower()
        if hallucination_type not in {"none", "entity", "relation_action", "fabrication"}:
            hallucination_type = "none" if status == "supported" else "fabrication"
        normalized.append(
            {
                "claim_id": claim_id,
                "claim_text": claim["claim_text"],
                "claim_type": claim.get("claim_type", "fact"),
                "importance": _normalize_importance(claim.get("importance", 3)),
                "status": status,
                "hallucination_type": hallucination_type,
                "confidence": min(max(_safe_int(matched.get("confidence", 70), 70), 0), 100),
                "evidence": str(matched.get("evidence", "")).strip(),
                "reason": str(matched.get("reason", "")).strip(),
            }
        )
    return normalized


def score_claim_based_hallucination(
    generated_summary: str,
    reference_evidence: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    if not reference_evidence.strip():
        return {
            "score": None,
            "label": "na",
            "details": "reference evidence missing",
            "claim_count": 0,
            "total_claim_importance": 0,
            "supported_claim_count": 0,
            "contradicted_claim_count": 0,
            "not_mentioned_claim_count": 0,
            "supported_claim_importance": 0,
            "contradicted_claim_importance": 0,
            "not_mentioned_claim_importance": 0,
            "support_ratio": None,
            "judge_confidence": None,
            "hallucination_density": None,
            "weighted_hallucination_density": None,
            "weighted_penalty": 0.0,
            "hallucination_breakdown": {
                "entity": 0,
                "relation_action": 0,
                "fabrication": 0,
            },
            "weighted_hallucination_penalty_breakdown": {
                "entity": 0.0,
                "relation_action": 0.0,
                "fabrication": 0.0,
            },
            "claims": [],
        }

    claims = extract_claims(
        generated_summary=generated_summary,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )
    if not claims:
        score = 0.5
        return {
            "score": score,
            "label": _label_from_score(score),
            "details": "no verifiable claims extracted",
            "claim_count": 0,
            "total_claim_importance": 0,
            "supported_claim_count": 0,
            "contradicted_claim_count": 0,
            "not_mentioned_claim_count": 0,
            "supported_claim_importance": 0,
            "contradicted_claim_importance": 0,
            "not_mentioned_claim_importance": 0,
            "support_ratio": None,
            "judge_confidence": None,
            "hallucination_density": None,
            "weighted_hallucination_density": None,
            "weighted_penalty": 0.0,
            "hallucination_breakdown": {
                "entity": 0,
                "relation_action": 0,
                "fabrication": 0,
            },
            "weighted_hallucination_penalty_breakdown": {
                "entity": 0.0,
                "relation_action": 0.0,
                "fabrication": 0.0,
            },
            "claims": [],
        }

    verifications = verify_claims(
        claims=claims,
        reference_evidence=reference_evidence,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )

    claim_count = len(verifications)
    total_claim_importance = _sum_importance(verifications)
    supported_count = sum(1 for item in verifications if item["status"] == "supported")
    contradicted_count = sum(1 for item in verifications if item["status"] == "contradicted")
    not_mentioned_count = sum(1 for item in verifications if item["status"] == "not_mentioned")

    supported_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["status"] == "supported"
    )
    contradicted_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["status"] == "contradicted"
    )
    not_mentioned_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["status"] == "not_mentioned"
    )

    entity_count = sum(1 for item in verifications if item["hallucination_type"] == "entity")
    relation_count = sum(1 for item in verifications if item["hallucination_type"] == "relation_action")
    fabrication_count = sum(1 for item in verifications if item["hallucination_type"] == "fabrication")

    weighted_entity_penalty = sum(
        ENTITY_HALLUCINATION_PENALTY * _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["hallucination_type"] == "entity"
    )
    weighted_relation_penalty = sum(
        RELATION_HALLUCINATION_PENALTY * _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["hallucination_type"] == "relation_action"
    )
    weighted_fabrication_penalty = sum(
        FABRICATION_HALLUCINATION_PENALTY * _normalize_importance(item.get("importance", 3))
        for item in verifications
        if item["hallucination_type"] == "fabrication"
    )

    weighted_penalty = weighted_entity_penalty + weighted_relation_penalty + weighted_fabrication_penalty
    support_ratio = supported_importance / total_claim_importance if total_claim_importance else 0.0
    weighted_hallucination_density = weighted_penalty / total_claim_importance if total_claim_importance else 0.0
    hallucination_density = (contradicted_count + not_mentioned_count) / claim_count if claim_count else 0.0
    confidence = (
        sum(item["confidence"] for item in verifications) / (100.0 * claim_count) if claim_count else 0.0
    )
    capped_weighted_hallucination_density = min(weighted_hallucination_density, FACT_WEIGHTED_PENALTY_CAP)
    score = max(
        0.0,
        round(
            FACT_SUPPORT_WEIGHT * support_ratio
            + FACT_ANTI_HALLUCINATION_WEIGHT * (1.0 - capped_weighted_hallucination_density)
            + FACT_CONFIDENCE_WEIGHT * confidence,
            4,
        ),
    )
    label = _label_from_score(score)

    return {
        "score": score,
        "label": label,
        "details": "",
        "claim_count": claim_count,
        "total_claim_importance": total_claim_importance,
        "supported_claim_count": supported_count,
        "contradicted_claim_count": contradicted_count,
        "not_mentioned_claim_count": not_mentioned_count,
        "supported_claim_importance": supported_importance,
        "contradicted_claim_importance": contradicted_importance,
        "not_mentioned_claim_importance": not_mentioned_importance,
        "support_ratio": round(support_ratio, 4),
        "judge_confidence": round(confidence, 4),
        "hallucination_density": round(hallucination_density, 4),
        "weighted_hallucination_density": round(weighted_hallucination_density, 4),
        "weighted_penalty": round(weighted_penalty, 4),
        "capped_weighted_hallucination_density": round(capped_weighted_hallucination_density, 4),
        "hallucination_breakdown": {
            "entity": entity_count,
            "relation_action": relation_count,
            "fabrication": fabrication_count,
        },
        "weighted_hallucination_penalty_breakdown": {
            "entity": round(weighted_entity_penalty, 4),
            "relation_action": round(weighted_relation_penalty, 4),
            "fabrication": round(weighted_fabrication_penalty, 4),
        },
        "claims": verifications,
    }


def extract_task_requirements(
    user_prompt: str,
    human_guidance: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    normalized_user_prompt = user_prompt.strip()
    normalized_human_guidance = human_guidance.strip()
    requirements_text = "\n\n".join(
        part for part in [normalized_user_prompt, normalized_human_guidance] if part.strip()
    )
    if not requirements_text:
        return []

    cache_key = (normalized_user_prompt, normalized_human_guidance, base_url, _get_model_name(model_name))
    with _TASK_REQUIREMENTS_CACHE_LOCK:
        cached = _TASK_REQUIREMENTS_CACHE.get(cache_key)
    if cached is not None:
        return deepcopy(cached)

    client = _get_client(api_key=api_key, base_url=base_url)
    system_prompt = (
        "你是一名任务需求拆解器。"
        "请从用户提示和人工指导中抽取可评估的独立任务要求。"
        "输出 JSON 对象，字段 requirements 为数组。"
        "每个元素必须包含 requirement_id、requirement_text、importance。"
        "importance 取 1 到 5。"
    )
    user_input = f"[instructions]\n{requirements_text}"
    payload = _chat_json(client, system_prompt, user_input, model_name=model_name)
    normalized = _normalize_requirements(payload.get("requirements", []))

    with _TASK_REQUIREMENTS_CACHE_LOCK:
        _TASK_REQUIREMENTS_CACHE[cache_key] = deepcopy(normalized)
    return normalized


def score_task_alignment(
    generated_summary: str,
    user_prompt: str,
    human_guidance: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    requirements = extract_task_requirements(
        user_prompt=user_prompt,
        human_guidance=human_guidance,
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
    )
    if not requirements:
        return {
            "score": None,
            "label": "na",
            "details": "user_prompt and human_guidance missing",
            "requirement_count": 0,
            "total_requirement_importance": 0,
            "satisfied_requirement_count": 0,
            "partial_requirement_count": 0,
            "unsatisfied_requirement_count": 0,
            "satisfied_requirement_importance": 0,
            "partial_requirement_importance": 0,
            "unsatisfied_requirement_importance": 0,
            "coverage_ratio": None,
            "judge_confidence": None,
            "requirements": [],
        }

    client = _get_client(api_key=api_key, base_url=base_url)
    system_prompt = (
        "你是一名细粒度任务对齐裁判。"
        "请基于生成总结，逐条评估任务要求是否被满足。"
        "输出 JSON 对象，字段 evaluations 为数组。"
        "每个元素必须包含 requirement_id、status、confidence、evidence、reason。"
        "status 只能是 satisfied、partial、unsatisfied。"
        "confidence 取 0 到 100 的整数。"
    )
    user_input = (
        f"[requirements]\n{json.dumps(requirements, ensure_ascii=False, indent=2)}\n\n"
        f"[generated_summary]\n{generated_summary}"
    )
    payload = _chat_json(client, system_prompt, user_input, model_name=model_name)
    raw_rows = payload.get("evaluations", [])

    normalized: List[Dict[str, Any]] = []
    for requirement in requirements:
        requirement_id = requirement["requirement_id"]
        matched = None
        if isinstance(raw_rows, list):
            for row in raw_rows:
                if isinstance(row, dict) and str(row.get("requirement_id", "")).strip() == requirement_id:
                    matched = row
                    break
        if matched is None:
            matched = {}
        status = str(matched.get("status", "unsatisfied")).strip().lower()
        if status not in {"satisfied", "partial", "unsatisfied"}:
            status = "unsatisfied"
        normalized.append(
            {
                "requirement_id": requirement_id,
                "requirement_text": requirement["requirement_text"],
                "importance": _normalize_importance(requirement.get("importance", 3)),
                "status": status,
                "confidence": min(max(_safe_int(matched.get("confidence", 70), 70), 0), 100),
                "evidence": str(matched.get("evidence", "")).strip(),
                "reason": str(matched.get("reason", "")).strip(),
            }
        )

    requirement_count = len(normalized)
    total_requirement_importance = _sum_importance(normalized)
    satisfied_count = sum(1 for item in normalized if item["status"] == "satisfied")
    partial_count = sum(1 for item in normalized if item["status"] == "partial")
    unsatisfied_count = sum(1 for item in normalized if item["status"] == "unsatisfied")

    satisfied_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in normalized
        if item["status"] == "satisfied"
    )
    partial_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in normalized
        if item["status"] == "partial"
    )
    unsatisfied_importance = sum(
        _normalize_importance(item.get("importance", 3))
        for item in normalized
        if item["status"] == "unsatisfied"
    )

    if total_requirement_importance > 0:
        earned_score = satisfied_importance + partial_importance * TASK_PARTIAL_CREDIT_WEIGHT
        coverage_ratio = earned_score / total_requirement_importance
    else:
        coverage_ratio = 0.0

    confidence = (
        sum(item["confidence"] for item in normalized) / (100.0 * requirement_count)
        if requirement_count
        else 0.0
    )
    score = round(TASK_COVERAGE_WEIGHT * coverage_ratio + TASK_CONFIDENCE_WEIGHT * confidence, 4)
    label = _label_from_score(score)

    return {
        "score": score,
        "label": label,
        "details": "",
        "requirement_count": requirement_count,
        "total_requirement_importance": total_requirement_importance,
        "satisfied_requirement_count": satisfied_count,
        "partial_requirement_count": partial_count,
        "unsatisfied_requirement_count": unsatisfied_count,
        "satisfied_requirement_importance": satisfied_importance,
        "partial_requirement_importance": partial_importance,
        "unsatisfied_requirement_importance": unsatisfied_importance,
        "coverage_ratio": round(coverage_ratio, 4),
        "judge_confidence": round(confidence, 4),
        "requirements": normalized,
    }
