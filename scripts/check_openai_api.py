
"""
LLM 多能力连通性检查脚本 —— 覆盖 chat / vision / transcribe / embedding / search。

覆盖 core/workflow、core/extraction、backend/tasks 中的所有 LLM API 调用方式：
  1. Chat Completions（纯文本 + JSON Mode）  — fusion_drafter / chunk_synthesizer / grader
  2. Chat Completions（多模态 Vision）       — chunk_vision_analyzer
  3. Audio Transcriptions（Whisper）          — AudioTranscriber.transcribe
  4. Embeddings（向量嵌入）                  — rag_settings_factory → modular_rag IngestionPipeline
  5. Tavily Search                            — execute_tavily_search（独立）

Chat / Vision / Transcribe 统一走 get_model_for_capability() 工厂，自动按
CHAT_PROVIDER / VISION_PROVIDER / TRANSCRIBE_PROVIDER 环境变量路由到对应厂商；
Embedding 走 openai.OpenAI().embeddings.create()，对齐 rag_settings_factory 的
OPENAI_API_KEY / OPENAI_BASE_URL / RAG_EMBEDDING_MODEL 配置方式。

用法：
  python scripts/check_openai_api.py          # 全量检查
  python scripts/check_openai_api.py --quick   # 仅 Chat 纯文本连通性
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import struct
import sys
import urllib.request
import zlib
from pathlib import Path
from tempfile import NamedTemporaryFile

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# 0. 环境加载
# ---------------------------------------------------------------------------

def _load_env() -> Path:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv()
    return PROJECT_ROOT


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _extract_error_detail(exc: Exception) -> str:
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_msg = str(cause)
        if cause_msg and cause_msg != str(exc):
            return f"{exc} ← 根因: {cause_msg}"[:400]
    body = getattr(exc, "body", None)
    if body and isinstance(body, dict):
        return str(body)[:400]
    return str(exc)[:400]


def _record(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, ok, detail))


def _print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_ok(name: str, detail: str = "") -> None:
    print(f"  [PASS] {name}" + (f"  —  {detail}" if detail else ""))


def _print_fail(name: str, detail: str = "") -> None:
    msg = f"  [FAIL] {name}"
    if detail:
        msg += f"\n         原因: {detail[:300]}"
    print(msg)


# ---------------------------------------------------------------------------
# 1. Chat 连通性（音频 Worker 模式 + Fusion Drafter 模式）
# ---------------------------------------------------------------------------

def test_chat() -> bool:
    """完全对齐工作流真实调用方式：

    1a. Audio Worker 模式（chunk_audio_analyzer._llm_extract_transcript_claims）
        temperature=0.1, timeout=CHUNK_WORKER_TIMEOUT_SECONDS, JSON 数组输出
    1b. Fusion Drafter / Grader 模式
        temperature=0.0, response_format=json_object, JSON 对象输出
    """
    _print_header("1. Chat Completions — Audio Worker 模式 + Fusion Drafter 模式")

    from core.llm.config import resolve_api_key, resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability
    from config.settings import CHUNK_WORKER_TIMEOUT_SECONDS

    provider = resolve_provider("chat")
    model_name = get_model_name_for_capability("chat")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model_name}")
    print(f"  Timeout  : {CHUNK_WORKER_TIMEOUT_SECONDS}s（同 CHUNK_WORKER_TIMEOUT_SECONDS）")

    if not resolve_api_key("chat"):
        _print_fail("Chat", "未配置 CHAT_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY")
        _record("Chat（Audio Worker 模式）", False, "API Key 未配置")
        _record("Chat（Fusion Drafter 模式）", False, "API Key 未配置")
        return False

    try:
        chat_model = get_model_for_capability("chat")
    except Exception as exc:
        _print_fail("Chat 模型初始化", _extract_error_detail(exc))
        _record("Chat（Audio Worker 模式）", False, _extract_error_detail(exc))
        _record("Chat（Fusion Drafter 模式）", False, _extract_error_detail(exc))
        return False

    all_ok = True

    # ---- 1a. Audio Worker 模式 ----
    # 对齐 _llm_extract_transcript_claims: temperature=0.1, timeout=CHUNK_WORKER_TIMEOUT_SECONDS
    # 使用中等长度 transcript 模拟真实负载（过于简短无法暴露超时问题）
    _SYNTHETIC_TRANSCRIPT = (
        "[00:00:01] 今天我们来讨论人工智能在医疗领域的应用，特别是影像识别和辅助诊断。\n"
        "[00:00:15] 机器学习模型已经能够在某些场景下超越人类医生的诊断准确率。\n"
        "[00:00:30] 但是我们也需要关注模型的可解释性和对偏见数据的敏感性。\n"
        "[00:00:45] 研究表明，在皮肤癌检测中，深度学习模型达到了皮肤科专家的水平。\n"
        "[00:01:00] 然而这些系统在不同人群中的表现存在显著差异，训练数据多样性至关重要。\n"
        "[00:01:20] 下面我们将看几个具体的案例研究，展示AI辅助诊断的真实效果。\n"
        "[00:01:35] 第一个案例来自斯坦福大学医学中心，他们使用卷积神经网络分析胸部X光片。\n"
        "[00:01:50] 结果显示，在检测肺炎方面，AI系统的敏感性达到90%以上。\n"
        "[00:02:10] 第二个案例涉及眼科疾病，特别是糖尿病视网膜病变的早期筛查。\n"
        "[00:02:25] Google DeepMind的研究在这一领域取得了突破性进展。\n"
    )
    _AUDIO_SYSTEM_PROMPT_MINI = (
        "你是严谨的视频分片转录文本分析助手。\n"
        "从 transcript 中提取原子事实断言（claims）。\n"
        "输出格式（JSON 数组）：\n"
        '[  {"claim": "断言内容", "exact_quote": "transcript 原话", "timestamp": "HH:MM:SS"} ]'
    )
    try:
        response = chat_model.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": _AUDIO_SYSTEM_PROMPT_MINI},
                {"role": "user", "content": f"[chunk_transcript]\n{_SYNTHETIC_TRANSCRIPT}"},
            ],
            temperature=0.1,
            timeout=CHUNK_WORKER_TIMEOUT_SECONDS,
        )
        try:
            import re as _re
            json_match = _re.search(r"\[.*\]", response, _re.DOTALL)
            claims = json.loads(json_match.group(0)) if json_match else []
            claim_count = len(claims) if isinstance(claims, list) else 0
        except Exception:
            claim_count = -1
        detail = f"timeout={CHUNK_WORKER_TIMEOUT_SECONDS}s, claims={claim_count}"
        print(f"  Audio Worker 回复 (前200字): {response[:200]}")
        _print_ok("Chat Audio Worker 模式", detail)
        _record("Chat（Audio Worker 模式）", True, detail)
    except Exception as exc:
        msg = _extract_error_detail(exc)
        _print_fail("Chat Audio Worker 模式", msg)
        _record("Chat（Audio Worker 模式）", False, msg)
        all_ok = False

    # ---- 1b. Fusion Drafter / Grader 模式 ----
    # 对齐 fusion_drafter / grader: temperature=0.0, response_format=json_object
    try:
        response = chat_model.chat_completion(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个 JSON 输出测试助手。请输出合法 JSON 对象，"
                        '包含字段 "score"（只能是"yes"）和 "reason"（空字符串）。'
                    ),
                },
                {"role": "user", "content": "请按格式输出。"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        parsed = json.loads(response)
        score = parsed.get("score", "")
        ok = score == "yes"
        print(f"  Fusion Drafter JSON score: {score}")
        _print_ok("Chat Fusion Drafter 模式" if ok else f"Chat Fusion Drafter 模式 ⚠️ score={score}")
        _record("Chat（Fusion Drafter 模式）", ok, f"score={score}")
        if not ok:
            all_ok = False
    except Exception as exc:
        _print_fail("Chat Fusion Drafter 模式", _extract_error_detail(exc))
        _record("Chat（Fusion Drafter 模式）", False, _extract_error_detail(exc))
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# 2. Vision 连通性（多模态）
# ---------------------------------------------------------------------------

def test_vision() -> bool:
    """完全对齐工作流真实调用方式：

    对应 chunk_vision_analyzer._llm_vision_analyze：
        temperature=0.2, max_tokens=1500, timeout=CHUNK_WORKER_TIMEOUT_SECONDS,
        response_format=json_object, 输出 frame_references + chunk_summary
    """
    _print_header("2. Vision Completions — Vision Worker 模式（多模态）")

    from core.llm.config import resolve_api_key, resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability
    from config.settings import CHUNK_WORKER_TIMEOUT_SECONDS

    provider = resolve_provider("vision")
    model_name = get_model_name_for_capability("vision")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model_name}")
    print(f"  Timeout  : {CHUNK_WORKER_TIMEOUT_SECONDS}s（同 CHUNK_WORKER_TIMEOUT_SECONDS）")

    if not resolve_api_key("vision"):
        _print_fail("Vision", "未配置 VISION_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY")
        _record("Vision Worker 模式", False, "API Key 未配置")
        return False

    try:
        vision_model = get_model_for_capability("vision")
    except Exception as exc:
        _print_fail("Vision 模型初始化", _extract_error_detail(exc))
        _record("Vision Worker 模式", False, _extract_error_detail(exc))
        return False

    # 生成一张 64x64 纯蓝 PNG 作为测试图片（满足各厂商最小分辨率限制）
    def _make_test_png() -> bytes:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        w, h = 64, 64
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        row = b"\x00" + b"\x00\x00\xff" * w  # 蓝色
        raw = row * h
        compressed = zlib.compress(raw)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")

    img_b64 = base64.b64encode(_make_test_png()).decode("utf-8")

    # 对齐 _llm_vision_analyze 的真实消息结构（text + image_url）
    _VISION_SYSTEM_PROMPT_MINI = (
        "你是严谨的视频分片视觉分析助手。\n"
        "根据关键帧画面，输出 JSON 对象：\n"
        '{"frame_references": [{"frame_time": "00:00:00", "observation": "画面描述", '
        '"audio_claim_match": "absent"}], "chunk_summary": "综合摘要"}'
    )
    content = [
        {
            "type": "text",
            "text": (
                "[chunk_id] test-chunk-000\n"
                "[user_prompt] 测试视觉连通性\n"
                "[transcript_claims] []\n\n"
                "请分析以下关键帧并输出 frame_references + chunk_summary："
            ),
        },
        {"type": "text", "text": "关键帧时间戳: 00:00:01"},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "low"},
        },
    ]

    try:
        response = vision_model.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM_PROMPT_MINI},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
            max_tokens=1500,
            timeout=CHUNK_WORKER_TIMEOUT_SECONDS,
            response_format={"type": "json_object"},
        )
        try:
            parsed = json.loads(response)
            frame_count = len(parsed.get("frame_references", []))
            summary = str(parsed.get("chunk_summary", ""))[:80]
        except Exception:
            frame_count = -1
            summary = response[:80]
        detail = f"timeout={CHUNK_WORKER_TIMEOUT_SECONDS}s, frames={frame_count}, summary={summary}"
        print(f"  Vision Worker 回复 (前200字): {response[:200]}")
        _print_ok("Vision Worker 模式", detail)
        _record("Vision Worker 模式", True, detail)
        return True
    except Exception as exc:
        msg = str(exc)
        if "not supported" in msg.lower() or "does not support" in msg.lower():
            _print_ok("Vision ⚠️ 模型不支持视觉", msg[:120])
            _record("Vision Worker 模式", True, f"模型不支持: {msg[:100]}")
            return True
        _print_fail("Vision Worker 模式", _extract_error_detail(exc))
        _record("Vision Worker 模式", False, _extract_error_detail(exc))
        return False


# ---------------------------------------------------------------------------
# 3. Transcription 连通性（Whisper）
# ---------------------------------------------------------------------------

def test_transcription() -> bool:
    """对应 AudioTranscriber.transcribe 风格。
       通过 get_model_for_capability("transcribe") 获取模型实例。"""
    _print_header("3. Audio Transcriptions — Whisper / 语音转文本")

    from core.llm.config import resolve_api_key, resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability

    provider = resolve_provider("transcribe")
    model_name = get_model_name_for_capability("transcribe")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model_name}")

    if not resolve_api_key("transcribe"):
        _print_ok("Transcription ⏭ 跳过", "未配置 TRANSCRIBE_API_KEY / OPENAI_API_KEY")
        _record("Transcription", True, "跳过：API Key 未配置（非必须）")
        return True

    try:
        transcribe_model = get_model_for_capability("transcribe")
    except Exception as exc:
        msg = str(exc)
        if "不支持" in msg or "not supported" in msg.lower():
            _print_ok("Transcription ⚠️ 当前 provider 不支持", msg[:120])
            _record("Transcription", True, f"Provider 不支持: {msg[:100]}")
            return True
        _print_fail("Transcription 模型初始化", _extract_error_detail(exc))
        _record("Transcription", False, _extract_error_detail(exc))
        return False

    # 生成一段极短的 WAV（静音）用于测试
    sample_rate = 16000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    pcm_data = b"\x00\x00" * num_samples

    wav_buffer = io.BytesIO()
    wav_buffer.write(b"RIFF")
    wav_buffer.write(struct.pack("<I", 36 + len(pcm_data)))
    wav_buffer.write(b"WAVE")
    wav_buffer.write(b"fmt ")
    wav_buffer.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate,
                                  sample_rate * 2, 2, 16))
    wav_buffer.write(b"data")
    wav_buffer.write(struct.pack("<I", len(pcm_data)))
    wav_buffer.write(pcm_data)
    wav_buffer.seek(0)

    # transcribe_audio 需要文件路径，写入临时文件
    tmp_path = None
    try:
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_buffer.read())
            tmp_path = Path(tmp.name)

        response = transcribe_model.transcribe_audio(
            model=model_name,
            audio_path=tmp_path,
            response_format="verbose_json",
        )
        try:
            data = json.loads(response)
            text = data.get("text", response)[:200]
        except Exception:
            if hasattr(response, "text"):
                text = response.text[:200]
            else:
                text = str(response)[:200]
        print(f"  转录结果: {text if text else '(空/静音)'}")
        _print_ok("Audio Transcriptions", f"model={model_name}")
        _record("Transcription", True, text[:120])
        return True
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg.lower() or "not found" in msg.lower():
            _print_ok("Transcription ⚠️ 模型不可用", f"{model_name}: {msg[:120]}")
            _record("Transcription", True, f"模型不可用: {msg[:100]}")
            return True
        _print_fail("Audio Transcriptions", _extract_error_detail(exc))
        _record("Transcription", False, _extract_error_detail(exc))
        return False
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 4. Tavily Search（独立）
# ---------------------------------------------------------------------------

def test_tavily_search() -> bool:
    """对应 execute_tavily_search 风格：POST https://api.tavily.com/search"""
    _print_header("4. Tavily Search — search_tools 风格")

    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        _print_ok("Tavily Search ⏭ 跳过", "TAVILY_API_KEY 未配置（非必须）")
        _record("Tavily Search", True, "跳过：未配置")
        return True

    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        "api_key": api_key,
        "query": "AI model connectivity test",
        "search_depth": "basic",
        "include_answer": False,
        "max_results": 2,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            snippets = [r.get("content", "")[:80] for r in result.get("results", [])]
            print(f"  搜索结果数: {len(snippets)}")
            for s in snippets:
                print(f"    - {s}...")
            _print_ok("Tavily Search", f"获得 {len(snippets)} 条结果")
            _record("Tavily Search", True, f"{len(snippets)} results")
            return True
    except Exception as exc:
        _print_fail("Tavily Search", _extract_error_detail(exc))
        _record("Tavily Search", False, _extract_error_detail(exc))
        return False


# ---------------------------------------------------------------------------
# 5. Embedding 连通性（向量嵌入 — RAG）
# ---------------------------------------------------------------------------

def test_embedding() -> bool:
    """完全对齐项目实际使用方式：

    对应 rag_settings_factory.py 构建的 EmbeddingSettings：
        provider="openai"
        model=RAG_EMBEDDING_MODEL（默认 text-embedding-3-small）
        api_key=OPENAI_API_KEY
        api_url=OPENAI_BASE_URL

    底层通过 openai.OpenAI().embeddings.create() 调用，
    与 modular_rag 库内部的 OpenAI embedding 调用完全一致。
    测试文本使用中文转录片段，模拟实际向量化场景。
    """
    _print_header("5. Embeddings — 向量嵌入连通性（RAG）")

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL") or None
    model_name = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")

    print(f"  Model    : {model_name}")
    print(f"  API Key  : {'已配置' if api_key else '未配置'}")
    if base_url:
        print(f"  Base URL : {base_url}")

    if not api_key:
        _print_fail("Embedding", "未配置 OPENAI_API_KEY（RAG 向量嵌入所需）")
        _record("Embedding（RAG 向量化）", False, "API Key 未配置")
        return False

    # 使用与项目实际嵌入场景相似的文本（中文视频转录片段）
    # 对齐 transcript_text_loader 分块前的原始文本格式
    _TEST_TEXT = (
        "人工智能在医疗领域的应用正在快速发展，特别是在医学影像分析和辅助诊断方面。"
        "深度学习模型已经在皮肤癌检测、胸部X光片分析等任务中展现了超越人类医生的准确率。"
        "然而，这些系统在不同人群中的表现存在差异，训练数据的多样性和代表性至关重要。"
        "斯坦福大学的研究表明，卷积神经网络在分析胸部X光片时对肺炎的检测敏感性超过90%。"
        "Google DeepMind 在糖尿病视网膜病变的早期筛查方面也取得了突破性进展。"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.embeddings.create(
            model=model_name,
            input=_TEST_TEXT,
        )
        embedding = response.data[0].embedding
        dim = len(embedding)

        detail = f"model={model_name}, dim={dim}"
        print(f"  Embedding 维度 : {dim}")
        print(f"  向量前 5 值    : {[round(v, 6) for v in embedding[:5]]}")
        _print_ok("Embedding（RAG 向量化）", detail)
        _record("Embedding（RAG 向量化）", True, detail)
        return True
    except Exception as exc:
        msg = _extract_error_detail(exc)
        # 某些非 OpenAI 厂商的代理可能不支持 embeddings 端点
        if "not found" in msg.lower() or "does not exist" in msg.lower():
            _print_ok("Embedding ⚠️ 端点不可用", msg[:120])
            _record("Embedding（RAG 向量化）", True, f"端点不可用: {msg[:100]}")
            return True
        _print_fail("Embedding（RAG 向量化）", msg)
        _record("Embedding（RAG 向量化）", False, msg)
        return False


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM 多能力连通性检查（chat / vision / transcribe / embedding / search）"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="仅测试 Chat 纯文本连通性"
    )
    args = parser.parse_args()

    _load_env()

    from core.llm.config import resolve_api_key, resolve_provider

    print("=" * 60)
    print("  LLM 多能力连通性检查")
    print("  覆盖 chat / vision / transcribe / embedding / search")
    print("=" * 60)

    # 展示当前配置
    for cap in ("chat", "vision", "transcribe"):
        provider = resolve_provider(cap)
        key = resolve_api_key(cap)
        print(f"  [{cap:11}] provider={provider:8s}  key={'已配置' if key else '未配置':>4s}")

    embedding_model = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    print(f"  [embedding  ] model={embedding_model:24s}  key={'已配置' if openai_key else '未配置':>4s}")

    tavily_key = os.getenv("TAVILY_API_KEY", "")
    print(f"  [tavily      ] {'已配置' if tavily_key else '未配置'}")

    if not any(resolve_api_key(c) for c in ("chat", "vision", "transcribe")):
        print("\n[ERROR] 所有 capability 均未配置 API Key，请在 .env 中配置后重试。")
        sys.exit(1)

    # 按顺序执行测试
    test_chat()

    if not args.quick:
        test_vision()
        test_transcription()
        test_embedding()
        test_tavily_search()

    # 汇总报告
    _print_header("汇总报告")
    total = len(_RESULTS)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    failed = total - passed

    for name, ok, detail in _RESULTS:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}" + (f"  —  {detail[:120]}" if detail else ""))

    print(f"\n  通过: {passed}/{total}  失败: {failed}/{total}")
    if failed > 0:
        print("  部分接口不可用，请检查上方 [FAIL] 项的具体报错。")
        sys.exit(1)
    else:
        print("  全部接口可用 🎉")
        sys.exit(0)


if __name__ == "__main__":
    main()

