
import os
from pathlib import Path
from dotenv import load_dotenv


def _get_int_env(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _get_float_env(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _get_csv_env(name: str, default_csv: str) -> tuple[str, ...]:
    raw = os.getenv(name, default_csv)
    parts = [item.strip() for item in raw.split(",")]
    return tuple(item for item in parts if item)

# 加载 .env 文件中的环境变量
# 这会查找与此文件同级的 .env 文件，或者向上查找
# 我们将 .env 文件放在项目根目录
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)  # 从 .env 文件中加载环境变量（如 API 密钥）

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent  # 获取项目根目录的绝对路径
VIDEO_SUMMARY_ROOT = Path(os.getenv("VIDEO_SUMMARY_ROOT", str(BASE_DIR))).resolve()

# 临时文件目录
TEMP_DIR = BASE_DIR / "temp"
TEMP_VIDEO_DIR = TEMP_DIR / "videos"
TEMP_AUDIO_DIR = TEMP_DIR / "audios"
TEMP_FRAMES_DIR = TEMP_DIR / "frames"

# 本地对象存储根目录（与 backend/config.py 的 OSS_LOCAL_ROOT 对齐）
_oss_local_raw = os.getenv("OSS_LOCAL_ROOT", "temp/object_storage")
OSS_LOCAL_ROOT_PATH = (BASE_DIR / _oss_local_raw).resolve()

# 确保目录存在
for dir_path in [TEMP_VIDEO_DIR, TEMP_AUDIO_DIR, TEMP_FRAMES_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 默认配置
DEFAULT_FRAME_INTERVAL = 2  # 默认每2秒抽一帧
MAX_IMAGE_SIZE = 768        # 图片长边限制

# 转文本模型配置
TRANSCRIBER_MODEL = os.getenv("TRANSCRIBER_MODEL", "whisper-1")  # 语音转文本模型

# LLM provider 路由配置（按能力拆分）
CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "openai").strip().lower() or "openai"
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "openai").strip().lower() or "openai"
TRANSCRIBE_PROVIDER = os.getenv("TRANSCRIBE_PROVIDER", "openai").strip().lower() or "openai"

# Checkpoint 配置（5.2 第一阶段）
CHECKPOINT_BACKEND = os.getenv("CHECKPOINT_BACKEND", "memory")
CHECKPOINT_DB_URL = os.getenv("CHECKPOINT_DB_URL", "")

# 5.3 Map-Reduce（迭代 A）配置
MAP_CHUNK_SECONDS = int(os.getenv("MAP_CHUNK_SECONDS", "480"))
MAP_CHUNK_OVERLAP_SECONDS = int(os.getenv("MAP_CHUNK_OVERLAP_SECONDS", "10"))
MAP_MAX_PARALLELISM = int(os.getenv("MAP_MAX_PARALLELISM", "4"))
WAVE_DISPATCH_SIZE = _get_int_env("WAVE_DISPATCH_SIZE", MAP_MAX_PARALLELISM, minimum=1)

# 场景突变辅助分片配置
ENABLE_SCENE_ANCHORED_CHUNK_SPLIT = os.getenv("ENABLE_SCENE_ANCHORED_CHUNK_SPLIT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS = _get_int_env("CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS", 60, minimum=5)
CHUNK_SPLIT_DURATION_SHORT_SECONDS = _get_int_env("CHUNK_SPLIT_DURATION_SHORT_SECONDS", 600, minimum=60)
CHUNK_SPLIT_DURATION_MEDIUM_SECONDS = _get_int_env(
    "CHUNK_SPLIT_DURATION_MEDIUM_SECONDS", 1800, minimum=CHUNK_SPLIT_DURATION_SHORT_SECONDS + 60
)
CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS = _get_int_env("CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS", 90, minimum=30)
CHUNK_SPLIT_MIN_DURATION_MEDIUM_SECONDS = _get_int_env(
    "CHUNK_SPLIT_MIN_DURATION_MEDIUM_SECONDS",
    300,
    minimum=CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS,
)
CHUNK_SPLIT_MIN_DURATION_LONG_SECONDS = _get_int_env(
    "CHUNK_SPLIT_MIN_DURATION_LONG_SECONDS",
    480,
    minimum=CHUNK_SPLIT_MIN_DURATION_MEDIUM_SECONDS,
)

# 场景突变分级阈值（基于 scene_change_score = 1 - correlation）
SCENE_CHANGE_SEVERE_SHORT_THRESHOLD = _get_float_env("SCENE_CHANGE_SEVERE_SHORT_THRESHOLD", 0.45, minimum=0.0)
SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD = _get_float_env("SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD", 0.35, minimum=0.0)
SCENE_CHANGE_SEVERE_LONG_THRESHOLD = _get_float_env("SCENE_CHANGE_SEVERE_LONG_THRESHOLD", 0.25, minimum=0.0)

# 5.6 第三阶段：波次并行容错配置
CHUNK_WORKER_TIMEOUT_SECONDS = float(os.getenv("CHUNK_WORKER_TIMEOUT_SECONDS", "120"))
if CHUNK_WORKER_TIMEOUT_SECONDS <= 0:
    CHUNK_WORKER_TIMEOUT_SECONDS = 45.0
CHUNK_WORKER_MAX_RETRIES = _get_int_env("CHUNK_WORKER_MAX_RETRIES", 1, minimum=0)
CHUNK_DEGRADED_MARKER = os.getenv("CHUNK_DEGRADED_MARKER", "<missing_context>").strip() or "<missing_context>"

# 5.7 第四阶段：上下文记忆滑动窗口配置
CONTEXT_MEMORY_WINDOW_SIZE = _get_int_env("CONTEXT_MEMORY_WINDOW_SIZE", 2, minimum=1)
CONTEXT_MEMORY_SUMMARY_MAX_CHARS = _get_int_env("CONTEXT_MEMORY_SUMMARY_MAX_CHARS", 160, minimum=60)

# 5.3 Map-Reduce（迭代 B）配置
CHUNK_MAX_TOOL_CALLS = int(os.getenv("CHUNK_MAX_TOOL_CALLS", "2"))
ENABLE_CHUNK_CACHE = os.getenv("ENABLE_CHUNK_CACHE", "true").strip().lower() in {"1", "true", "yes", "on"}
AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS = int(os.getenv("AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS", "100000"))

# 5.5 Outline Bootstrap 配置（第一阶段工程化）
OUTLINE_STOPWORDS_DIR = Path(
    os.getenv("OUTLINE_STOPWORDS_DIR", str(VIDEO_SUMMARY_ROOT / "data" / "stopwords"))
).resolve()
OUTLINE_ZH_STOPWORDS_PATH = OUTLINE_STOPWORDS_DIR / "zh.txt"
OUTLINE_EN_STOPWORDS_PATH = OUTLINE_STOPWORDS_DIR / "en.txt"
OUTLINE_TRIM_RULES_PATH = OUTLINE_STOPWORDS_DIR / "trim_rules.json"

OUTLINE_ENTITY_LIMIT_BASE = _get_int_env("OUTLINE_ENTITY_LIMIT_BASE", 12, minimum=1)
OUTLINE_ENTITY_LIMIT_MAX = _get_int_env("OUTLINE_ENTITY_LIMIT_MAX", 36, minimum=OUTLINE_ENTITY_LIMIT_BASE)
OUTLINE_ENTITY_LIMIT_STEP = _get_int_env("OUTLINE_ENTITY_LIMIT_STEP", 4, minimum=1)
OUTLINE_ENTITY_LIMIT_EVERY_SECONDS = _get_int_env("OUTLINE_ENTITY_LIMIT_EVERY_SECONDS", 1800, minimum=300)

OUTLINE_JIEBA_TOPK = _get_int_env("OUTLINE_JIEBA_TOPK", 12, minimum=1)
OUTLINE_MIN_TOKEN_LENGTH = _get_int_env("OUTLINE_MIN_TOKEN_LENGTH", 2, minimum=1)
OUTLINE_MAX_TOKEN_LENGTH = _get_int_env("OUTLINE_MAX_TOKEN_LENGTH", 12, minimum=2)
OUTLINE_ZH_REGEX_MIN = _get_int_env("OUTLINE_ZH_REGEX_MIN", 2, minimum=1)
OUTLINE_ZH_REGEX_MAX = _get_int_env("OUTLINE_ZH_REGEX_MAX", 12, minimum=OUTLINE_ZH_REGEX_MIN)

OUTLINE_DOMAIN_SUFFIX_MARKERS = _get_csv_env("OUTLINE_DOMAIN_SUFFIX_MARKERS", "")

# 方案B阶段2：运行指标采样配置
ENABLE_METRICS_LOGGING = os.getenv("ENABLE_METRICS_LOGGING", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
METRICS_SAMPLE_RATE = float(os.getenv("METRICS_SAMPLE_RATE", "1.0"))
if METRICS_SAMPLE_RATE < 0:
    METRICS_SAMPLE_RATE = 0.0
if METRICS_SAMPLE_RATE > 1:
    METRICS_SAMPLE_RATE = 1.0

# 方案B阶段3：keyframes 引用化 PoC 配置（默认关闭，保持兼容）
ENABLE_KEYFRAME_FILE_REFERENCE = os.getenv("ENABLE_KEYFRAME_FILE_REFERENCE", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
KEYFRAME_REFERENCE_INCLUDE_INLINE_IMAGE = os.getenv(
    "KEYFRAME_REFERENCE_INCLUDE_INLINE_IMAGE", "false"
).strip().lower() in {"1", "true", "yes", "on"}
KEYFRAME_IMAGE_EXTENSION = os.getenv("KEYFRAME_IMAGE_EXTENSION", "jpg").strip().lower() or "jpg"

# Self-RAG 质量闭环熔断配置
SELF_RAG_MAX_REVISIONS = _get_int_env("SELF_RAG_MAX_REVISIONS", 2, minimum=1)

# 并发模式配置（架构瘦身后固定为 send_api）
CONCURRENCY_MODE = "send_api"
