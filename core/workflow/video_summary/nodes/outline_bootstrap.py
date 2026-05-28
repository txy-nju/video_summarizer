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
from core.llm.factory import get_model_for_capability
from core.workflow.video_summary.state import VideoSummaryState

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


_NARRATIVE_ARC_SYSTEM_PROMPT = (
    "你是一个视频内容分析助手。\n"
    "你的任务是：根据给定的视频语音转录稿，提取该视频的叙事章节大纲（narrative arc）。\n\n"
    "【严格约束】：\n"
    "1. 🚫 禁止臆造：每个章节的 summary 必须严格来源于转录稿原文，不得补充任何未提及的内容。\n"
    "2. ⏱ 时间对齐：start_sec / end_sec 必须与转录稿中的时间戳对齐，不得随意估算。\n"
    "3. 📦 JSON 输出：直接输出 JSON 数组，不要包裹在 markdown 代码块中，不要有其他说明文字。\n\n"
    "输出格式（JSON 数组）：\n"
    "[\n"
    "  {\"chapter_id\": \"ch_0\", \"title\": \"章节标题\", \"start_sec\": 0, \"end_sec\": 120, \"summary\": \"基于转录稿的简短描述\"},\n"
    "  ...\n"
    "]"
)


def _build_transcript_text_for_llm(transcript_items: List[Dict[str, Any]]) -> str:
    """将 transcript 段落列表拼接成适合 LLM 输入的带时间戳纯文本。"""
    lines: List[str] = []
    for item in transcript_items:
        start = item.get("start", item.get("timestamp", 0))
        text = str(item.get("text", "")).strip()
        if text:
            lines.append(f"[{start:.1f}s] {text}")
    return "\n".join(lines)


def _extract_narrative_arc_llm(
    transcript_items: List[Dict[str, Any]],
    llm_model=None,
) -> List[Dict[str, Any]]:
    """
    调用轻量 LLM 从 transcript 提取 narrative_arc 章节列表。

    失败时静默降级，返回空列表，不阻断主流程。
    """
    if not transcript_items:
        return []

    transcript_text = _build_transcript_text_for_llm(transcript_items)
    if not transcript_text.strip():
        return []

    try:
        model_client = llm_model or get_model_for_capability("chat")
        response = model_client.invoke([
            {"role": "system", "content": _NARRATIVE_ARC_SYSTEM_PROMPT},
            {"role": "user", "content": f"以下是视频转录稿，请提取叙事章节大纲：\n\n{transcript_text}"},
        ])
        raw_text = response.content if hasattr(response, "content") else str(response)
        raw_text = raw_text.strip()

        # 尝试提取 JSON 数组（兼容模型在输出前后加额外文字的情况）
        json_match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if json_match:
            raw_text = json_match.group(0)

        chapters = json.loads(raw_text)
        if not isinstance(chapters, list):
            return []

        validated: List[Dict[str, Any]] = []
        for ch in chapters:
            if not isinstance(ch, dict):
                continue
            validated.append({
                "chapter_id": str(ch.get("chapter_id", f"ch_{len(validated)}")),
                "title": str(ch.get("title", "")).strip(),
                "start_sec": int(ch.get("start_sec", 0)),
                "end_sec": int(ch.get("end_sec", 0)),
                "summary": str(ch.get("summary", "")).strip(),
            })
        return validated

    except Exception:
        return []


def outline_bootstrap_node(state: VideoSummaryState, llm_model=None) -> dict:
    """
    全局上下文引导节点。

    目标:
    - 调用 LLM 从完整 transcript 提取 narrative_arc（按时间轴划分的叙事章节）。
    - LLM 失败时降级为空列表，不阻断主流程。
    - 同时保留确定性实体/时间锚点提取，写入 reduce_debug_info 供观测使用。
    """
    transcript = str(state.get("transcript", ""))
    chunk_plan = state.get("chunk_plan", [])
    if not isinstance(chunk_plan, list):
        chunk_plan = []

    transcript_data = _load_transcript_data(transcript)
    transcript_items = _collect_transcript_items(transcript_data)

    # 确定性结构提取（仅用于 debug 观测，不再写入主状态字段）
    entities = _collect_entity_candidates(transcript_items, chunk_plan)
    timeline_anchors = _build_timeline_anchors(chunk_plan)

    # LLM 提取 narrative_arc，失败时降级为 []
    print("  -> [Outline Bootstrap] Extracting narrative arc via LLM...")
    narrative_arc = _extract_narrative_arc_llm(transcript_items, llm_model=llm_model)
    if narrative_arc:
        print(f"  -> [Outline Bootstrap] narrative_arc extracted: {len(narrative_arc)} chapters.")
    else:
        print("  -> [Outline Bootstrap] narrative_arc extraction failed or empty, proceeding with [].")

    reduce_debug_info = state.get("reduce_debug_info", {})
    if not isinstance(reduce_debug_info, dict):
        reduce_debug_info = {}
    reduce_debug_info.update(
        {
            "outline_bootstrap_ready": True,
            "outline_entity_count": len(entities),
            "outline_timeline_anchor_count": len(timeline_anchors),
            "outline_narrative_arc_chapter_count": len(narrative_arc),
        }
    )

    return {
        "narrative_arc": narrative_arc,
        "reduce_debug_info": reduce_debug_info,
    }