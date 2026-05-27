import json
import re
from collections import Counter
from typing import Any, Dict, List

from config.settings import (
    OUTLINE_DOMAIN_SUFFIX_MARKERS,
    OUTLINE_EN_STOPWORDS_PATH,
    OUTLINE_ENTITY_LIMIT_BASE,
    OUTLINE_ENTITY_LIMIT_EVERY_SECONDS,
    OUTLINE_ENTITY_LIMIT_MAX,
    OUTLINE_ENTITY_LIMIT_STEP,
    OUTLINE_JIEBA_TOPK,
    OUTLINE_MAX_TOKEN_LENGTH,
    OUTLINE_MIN_TOKEN_LENGTH,
    OUTLINE_TRIM_RULES_PATH,
    OUTLINE_ZH_REGEX_MAX,
    OUTLINE_ZH_REGEX_MIN,
    OUTLINE_ZH_STOPWORDS_PATH,
)
from core.workflow.video_summary.state import VideoSummaryState
from core.llm.base import BaseModel
from core.llm.config import resolve_api_key
from core.llm.factory import get_model_for_capability, get_model_name_for_capability
from backend.observability.llm_tracing import trace_llm_call

try:
    import jieba
    import jieba.analyse as jieba_analyse
except Exception:
    jieba = None
    jieba_analyse = None


_INLINE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "have",
    "will",
    "your",
    "about",
    "video",
    "chunk",
    "then",
    "than",
    "when",
    "what",
    "where",
    "which",
    "一个",
    "我们",
    "你们",
    "他们",
    "这个",
    "那个",
    "以及",
    "然后",
    "因为",
    "所以",
    "可以",
    "进行",
    "我觉得",
    "你看",
    "你看这个",
    "就是说",
    "其实",
    "然后呢",
    "这个地方",
    "那个地方",
}

def _load_stopwords() -> set[str]:
    stopwords = {item.strip().lower() for item in _INLINE_STOPWORDS if item.strip()}
    for path in (OUTLINE_ZH_STOPWORDS_PATH, OUTLINE_EN_STOPWORDS_PATH):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                normalized = line.strip().lower()
                if normalized and not normalized.startswith("#"):
                    stopwords.add(normalized)
        except Exception:
            continue
    return stopwords


_STOPWORDS = _load_stopwords()

if jieba_analyse is not None and OUTLINE_ZH_STOPWORDS_PATH.exists():
    try:
        jieba_analyse.set_stop_words(str(OUTLINE_ZH_STOPWORDS_PATH))
    except Exception:
        pass


def _load_trim_rules() -> Dict[str, tuple[str, ...]]:
    default_rules = {"noisy_prefixes": tuple(), "noisy_suffixes": tuple()}
    if not OUTLINE_TRIM_RULES_PATH.exists():
        return default_rules

    try:
        data = json.loads(OUTLINE_TRIM_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_rules

    if not isinstance(data, dict):
        return default_rules

    def _to_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, list):
            return tuple()
        normalized: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                normalized.append(item.strip())
        return tuple(normalized)

    return {
        "noisy_prefixes": _to_tuple(data.get("noisy_prefixes", [])),
        "noisy_suffixes": _to_tuple(data.get("noisy_suffixes", [])),
    }


_TRIM_RULES = _load_trim_rules()


def _load_transcript_data(transcript: str) -> Dict[str, Any]:
    if not transcript or not transcript.strip():
        return {}

    try:
        data = json.loads(transcript)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _collect_transcript_items(transcript_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in ("segments", "chunks"):
        raw_items = transcript_data.get(key, [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict):
                items.append(item)
    return items


def _is_valid_candidate(token: str) -> bool:
    normalized = token.strip()
    if len(normalized) < OUTLINE_MIN_TOKEN_LENGTH:
        return False
    if len(normalized) > max(OUTLINE_MAX_TOKEN_LENGTH, OUTLINE_MIN_TOKEN_LENGTH):
        return False
    if normalized.isdigit():
        return False
    if normalized.lower() in _STOPWORDS:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized):
        noisy_prefixes = _TRIM_RULES.get("noisy_prefixes", tuple())
        noisy_suffixes = _TRIM_RULES.get("noisy_suffixes", tuple())
        if normalized.startswith(noisy_prefixes) or normalized.endswith(noisy_suffixes):
            return False
    return True


def _extract_english_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    for token in re.findall(r"\b[A-Z]{2,}\b|\b[A-Za-z]+(?:[A-Z][A-Za-z]+)+\b", text):
        if _is_valid_candidate(token):
            candidates.append(token.strip())
    return candidates


def _extract_chinese_candidates(text: str) -> List[str]:
    candidates: List[str] = []

    if jieba_analyse is not None:
        try:
            for token in jieba_analyse.extract_tags(text, topK=OUTLINE_JIEBA_TOPK, withWeight=False):
                normalized = str(token).strip()
                if re.search(r"[\u4e00-\u9fff]", normalized) and _is_valid_candidate(normalized):
                    candidates.append(normalized)
        except Exception:
            candidates = []

    if jieba is not None:
        try:
            for token in jieba.cut_for_search(text):
                normalized = str(token).strip()
                if re.search(r"[\u4e00-\u9fff]", normalized) and _is_valid_candidate(normalized):
                    candidates.append(normalized)
        except Exception:
            pass

    if candidates:
        candidates.extend(_extract_chinese_phrase_candidates(text))
        deduped: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    for token in _extract_chinese_phrase_candidates(text):
        if _is_valid_candidate(token):
            candidates.append(token.strip())
    return candidates


def _extract_chinese_phrase_candidates(text: str) -> List[str]:
    cleaned = str(text)
    chinese_stopwords = [word for word in _STOPWORDS if re.search(r"[\u4e00-\u9fff]", word)]
    for stopword in sorted(chinese_stopwords, key=len, reverse=True):
        cleaned = cleaned.replace(stopword, " ")

    phrases: List[str] = []
    for phrase in re.findall(rf"[\u4e00-\u9fff]{{{OUTLINE_ZH_REGEX_MIN},{OUTLINE_ZH_REGEX_MAX}}}", cleaned):
        normalized = phrase.strip()
        if not _is_valid_candidate(normalized):
            continue
        if OUTLINE_DOMAIN_SUFFIX_MARKERS and any(marker in normalized for marker in OUTLINE_DOMAIN_SUFFIX_MARKERS):
            normalized = normalized[: max(OUTLINE_MIN_TOKEN_LENGTH, OUTLINE_MAX_TOKEN_LENGTH)]
        phrases.append(normalized)
    return phrases


def _resolve_entity_limit(chunk_plan: List[Dict[str, Any]]) -> int:
    duration_seconds = 0
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        try:
            end_sec = int(chunk.get("end_sec", 0) or 0)
        except Exception:
            end_sec = 0
        duration_seconds = max(duration_seconds, end_sec)

    growth_steps = duration_seconds // OUTLINE_ENTITY_LIMIT_EVERY_SECONDS
    dynamic_limit = OUTLINE_ENTITY_LIMIT_BASE + growth_steps * OUTLINE_ENTITY_LIMIT_STEP
    return min(max(OUTLINE_ENTITY_LIMIT_BASE, dynamic_limit), OUTLINE_ENTITY_LIMIT_MAX)


def _collect_entity_candidates(transcript_items: List[Dict[str, Any]], chunk_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter: Counter[str] = Counter()
    sample_indexes: Dict[str, int] = {}

    for index, item in enumerate(transcript_items):
        text = str(item.get("text", "")).strip()
        if not text:
            continue

        seen_in_segment: set[str] = set()
        for token in _extract_english_candidates(text) + _extract_chinese_candidates(text):
            normalized = token.strip()
            if normalized in seen_in_segment:
                continue
            seen_in_segment.add(normalized)
            counter[normalized] += 1
            sample_indexes.setdefault(normalized, index)

    entities: List[Dict[str, Any]] = []
    for name, frequency in counter.most_common(_resolve_entity_limit(chunk_plan)):
        entities.append(
            {
                "name": name,
                "kind": "observed_term",
                "frequency": frequency,
                "source": "transcript",
                "sample_transcript_index": sample_indexes.get(name, -1),
            }
        )

    return entities


def _build_timeline_anchors(chunk_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anchors: List[Dict[str, Any]] = []
    for chunk in chunk_plan:
        if not isinstance(chunk, dict):
            continue
        chunk_id = str(chunk.get("chunk_id", "")).strip()
        if not chunk_id:
            continue
        anchors.append(
            {
                "chunk_id": chunk_id,
                "start_sec": int(chunk.get("start_sec", 0) or 0),
                "end_sec": int(chunk.get("end_sec", 0) or 0),
                "transcript_segment_indexes": list(chunk.get("transcript_segment_indexes", []) or []),
                "keyframe_indexes": list(chunk.get("keyframe_indexes", []) or []),
            }
        )
    return anchors


def _extract_narrative_arc(transcript_text: str, user_prompt: str, trace_id: str) -> List[Dict[str, Any]]:
    fallback = [{"chapter_title": "全局大纲(未切分)", "start_sec": 0, "end_sec": 999999}]
    if not transcript_text or not resolve_api_key("chat"):
        return fallback

    model_client = get_model_for_capability("chat")
    model_name = get_model_name_for_capability("chat")
    system_prompt = (
        "你是严谨的视频大纲提取助手。请根据提供的逐字稿提取全文的故事线大纲。\n"
        "1. 将视频逻辑划分为几个主要章节(chapter_title)。\n"
        "2. 每个章节必须推断出精确的起始秒数(start_sec)和结束秒数(end_sec)。\n"
        "3. 输出必须是JSON对象，包含 narrative_arc 数组。例如：{\"narrative_arc\": [{\"chapter_title\": \"引言\", \"start_sec\": 0, \"end_sec\": 120}]}\n"
        "严禁捏造无关大纲。"
    )
    
    try:
        with trace_llm_call(
            provider="openai_compatible",
            model=model_name,
            scope="outline_bootstrap",
            scope_id="global_arc",
            workflow_state="PLANNING",
        ):
            raw = model_client.chat_completion(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"[user_prompt]\n{user_prompt}\n\n[transcript]\n{transcript_text[:100000]}"}
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=60.0
            )
        parsed = json.loads(raw)
        arc = parsed.get("narrative_arc")
        if isinstance(arc, list) and arc:
            return arc
        return fallback
    except Exception:
        return fallback


def outline_bootstrap_node(state: VideoSummaryState) -> dict:
    """
    第一阶段的全局上下文引导节点。

    目标:
    - 仅输出结构化、非叙事的全局上下文。
    - 只保留实体候选与时间锚点，避免生成故事化摘要污染下游 worker。
    """
    transcript = str(state.get("transcript", ""))
    user_prompt = str(state.get("user_prompt", ""))
    trace_id = str(state.get("trace_id", ""))
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        chunk_plan = []

    transcript_data = _load_transcript_data(transcript)
    transcript_items = _collect_transcript_items(transcript_data)
    
    # 提取纯文本用于大纲生成
    transcript_text = "\n".join([str(item.get("text", "")).strip() for item in transcript_items if str(item.get("text", "")).strip()])

    structured_global_context = {
        "entities": _collect_entity_candidates(transcript_items, chunk_plan),
        "timeline_anchors": _build_timeline_anchors(chunk_plan),
        "narrative_arc": _extract_narrative_arc(transcript_text, user_prompt, trace_id),
        "source_policy": {
            "mode": "structured_only",
            "narrative_summary_allowed": False,
            "allowed_fields": ["entities", "timeline_anchors", "narrative_arc"],
        },
    }

    reduce_debug_info = state.get("reduce_debug_info", {})
    if not isinstance(reduce_debug_info, dict):
        reduce_debug_info = {}
    reduce_debug_info.update(
        {
            "outline_bootstrap_ready": True,
            "outline_entity_count": len(structured_global_context["entities"]),
            "outline_timeline_anchor_count": len(structured_global_context["timeline_anchors"]),
        }
    )

    return {
        "structured_global_context": structured_global_context,
        "reduce_debug_info": reduce_debug_info,
    }