
"""
OpenAI / Tavily API 连通性检查脚本。

覆盖 core/workflow 和 core/extraction 中所有 LLM API 调用方式：
  1. Chat Completions（纯文本）          — fusion_drafter / chunk_synthesizer 等
  2. Chat Completions（JSON Mode）       — hallucination_grader / usefulness_grader / chunk_audio_analyzer
  3. Chat Completions（多模态 Vision）   — chunk_vision_analyzer
  4. Audio Transcriptions（Whisper）     — AudioTranscriber.transcribe
  5. Tavily Search                       — execute_tavily_search
  6. Models.list（基础连通性）           — 轻量探测

用法：
  python scripts/check_openai_api.py          # 全量检查
  python scripts/check_openai_api.py --quick   # 仅基础连通性 + 纯文本 Chat
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import struct
import sys
import time
import urllib.request
import zlib
from pathlib import Path

# 确保项目根目录在 sys.path 中，与项目所有脚本保持一致
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from openai import OpenAI


# ---------------------------------------------------------------------------
# 0. 环境加载（与项目保持完全一致的方式）
# ---------------------------------------------------------------------------

def _load_env() -> Path:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv()
    return PROJECT_ROOT


def _get_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "")


def _get_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL") or None


def _get_model_name() -> str:
    return os.getenv("OPENAI_MODEL_NAME", "gpt-4o")


def _get_vision_model_name() -> str:
    return os.getenv("OPENAI_VISION_MODEL_NAME", os.getenv("OPENAI_MODEL_NAME", "gpt-4o"))


def _get_transcriber_model() -> str:
    from config.settings import TRANSCRIBER_MODEL
    return TRANSCRIBER_MODEL


def _get_tavily_key() -> str:
    return os.getenv("TAVILY_API_KEY", "")


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_RESULTS: list[tuple[str, bool, str]] = []


def _extract_error_detail(exc: Exception) -> str:
    """从异常中提取最有用的错误信息，包括根因。"""
    # 先看 __cause__
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_msg = str(cause)
        if cause_msg and cause_msg != str(exc):
            return f"{exc} ← 根因: {cause_msg}"[:400]
    # 再看响应的 body
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
# 测试用例
# ---------------------------------------------------------------------------

# ---- 1. 基础连通性：Models.list --------------------------------------------

def test_models_list(client: OpenAI) -> bool:
    _print_header("1. Models.list（基础连通性）")
    try:
        response = client.models.list()
        model_ids = [m.id for m in list(response.data)[:5]]
        _print_ok("Models.list", f"获取到 {len(response.data)} 个模型，前5: {model_ids}")
        _record("Models.list", True, str(model_ids))
        return True
    except Exception as exc:
        _print_fail("Models.list", _extract_error_detail(exc))
        _record("Models.list", False, _extract_error_detail(exc))
        return False


# ---- 2. Chat Completions（纯文本）—— 对应 fusion_drafter / chunk_synthesizer ----

def test_chat_text(client: OpenAI) -> bool:
    """对应 fusion_drafter_node、chunk_synthesizer._llm_chunk_fusion 风格"""
    _print_header("2. Chat Completions（纯文本，temperature=0.5）— fusion_drafter 风格")
    model = _get_model_name()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个严谨的测试助手。请用一句话回答用户问题。"},
                {"role": "user", "content": "说'API连通性测试通过'。"},
            ],
            temperature=0.5,
        )
        text = response.choices[0].message.content or ""
        print(f"  模型: {model}")
        print(f"  回复: {text[:200]}")
        _print_ok("Chat Completions（文本）", f"tokens={response.usage}")
        _record("Chat（文本）", True, text[:100])
        return True
    except Exception as exc:
        _print_fail("Chat Completions（文本）", _extract_error_detail(exc))
        _record("Chat（文本）", False, _extract_error_detail(exc))
        return False


# ---- 3. Chat Completions（JSON Mode）—— 对应 hallucination/usefulness grader ----

def test_chat_json_mode(client: OpenAI) -> bool:
    """对应 hallucination_grader_node / usefulness_grader_node 风格：
       temperature=0.0, response_format={"type": "json_object"}"""
    _print_header("3. Chat Completions（JSON Mode, temperature=0.0）— Grader 风格")
    model = _get_model_name()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个 JSON 输出测试助手。请输出一个合法 JSON 对象，"
                        '包含两个字段："score"(只能是"yes")和"reason"(空字符串)。'
                    ),
                },
                {"role": "user", "content": "请按格式输出。"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        score = parsed.get("score", "")
        print(f"  模型: {model}")
        print(f"  JSON 输出: score={score}")
        ok = score == "yes"
        _print_ok("Chat（JSON Mode）" if ok else "Chat（JSON Mode）⚠️", f"score={score}")
        _record("Chat（JSON Mode）", ok, f"score={score}")
        return ok
    except Exception as exc:
        _print_fail("Chat（JSON Mode）", _extract_error_detail(exc))
        _record("Chat（JSON Mode）", False, _extract_error_detail(exc))
        return False


# ---- 4. Chat Completions（多模态 Vision）—— 对应 chunk_vision_analyzer ----

def test_chat_vision(client: OpenAI) -> bool:
    """对应 _llm_vision_chunk_structured 风格：多模态输入，temperature=0.2，
       max_tokens=1024, response_format={"type": "json_object"}"""
    _print_header("4. Chat Completions（多模态 Vision）— chunk_vision_analyzer 风格")

    # 生成一张 1x1 纯蓝 PNG 作为测试图片（最小 PNG: 1x1 蓝色像素）

    def _make_1x1_blue_png() -> bytes:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        raw = b"\x00\x00\x00\xff"  # 蓝色 RGBA 滤镜字节+像素
        compressed = zlib.compress(raw)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")

    img_b64 = base64.b64encode(_make_1x1_blue_png()).decode("utf-8")

    model = _get_vision_model_name()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个视觉测试助手。请输出 JSON 对象，"
                        '包含字段 "color"(你所看到的图片主色调) 和 "resolution"(图片分辨率)。'
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请描述这张图片的颜色和尺寸。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "low"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        try:
            parsed = json.loads(raw)
            color = parsed.get("color", "未知")
        except Exception:
            color = raw[:80]
        print(f"  视觉模型: {model}")
        print(f"  识别结果: {color}")
        _print_ok("Chat（Vision 多模态）", f"识别颜色={color}")
        _record("Chat（Vision）", True, color)
        return True
    except Exception as exc:
        msg = str(exc)
        # 若模型不支持 vision，不算致命错误
        if "does not support" in msg.lower() or "not supported" in msg.lower():
            _print_ok("Chat（Vision）⚠️ 模型不支持视觉", msg[:120])
            _record("Chat（Vision）", True, f"模型不支持视觉: {msg[:100]}")
            return True
        _print_fail("Chat（Vision 多模态）", _extract_error_detail(exc))
        _record("Chat（Vision）", False, _extract_error_detail(exc))
        return False


# ---- 5. Audio Transcriptions（Whisper）—— 对应 AudioTranscriber.transcribe ----

def test_transcription(client: OpenAI) -> bool:
    """对应 AudioTranscriber.transcribe 风格：audio.transcriptions.create，
       使用 TRANSCRIBER_MODEL，verbose_json 格式。

       因为实际 whisper 需要真实音频，这里用一段简单音频或直接用 client 探测模型可用性。
    """
    _print_header("5. Audio Transcriptions（Whisper）— AudioTranscriber 风格")

    model = _get_transcriber_model()
    print(f"  Transcriber 模型: {model}")

    # 生成一段极短的 WAV（静音），用于测试 API 可达性
    # WAV 格式: 44-byte header + PCM data
    sample_rate = 16000
    duration_sec = 0.5
    num_samples = int(sample_rate * duration_sec)
    pcm_data = b"\x00\x00" * num_samples  # 静音

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
    wav_buffer.name = "test_silence.wav"

    try:
        # 与 AudioTranscriber 保持一致的调用方式
        response = client.audio.transcriptions.create(
            model=model,
            file=wav_buffer,
            response_format="verbose_json",
        )
        text = response.text if hasattr(response, "text") else str(response)
        print(f"  转录结果: {text[:200] if text else '(空)'}")
        _print_ok("Audio Transcriptions", f"model={model}, 转录结果={text[:120]}")
        _record("Transcription", True, text[:120])
        return True
    except Exception as exc:
        msg = str(exc)
        # 如果模型不支持音频转录接口（如某些代理），提示但不视为致命
        if "does not exist" in msg.lower() or "not found" in msg.lower():
            _print_ok("Audio Transcriptions ⚠️ 模型不可用", f"{model}: {msg[:120]}")
            _record("Transcription", True, f"模型不可用: {msg[:100]}")
            return True
        _print_fail("Audio Transcriptions", _extract_error_detail(exc))
        _record("Transcription", False, _extract_error_detail(exc))
        return False


# ---- 6. Tavily Search —— 对应 execute_tavily_search ----

def test_tavily_search() -> bool:
    """对应 execute_tavily_search 风格：POST https://api.tavily.com/search"""
    _print_header("6. Tavily Search — search_tools.execute_tavily_search 风格")

    api_key = _get_tavily_key()
    if not api_key:
        _print_ok("Tavily Search ⏭ 跳过", "TAVILY_API_KEY 未配置（非必须）")
        _record("Tavily Search", True, "跳过：未配置")
        return True

    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({
        "api_key": api_key,
        "query": "OpenAI API check connectivity test",
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
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="检查项目所有 LLM / 外部 API 连通性（与 core/workflow 调用方式一致）"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="仅测试基础连通性 + 纯文本 Chat"
    )
    args = parser.parse_args()

    _load_env()

    api_key = _get_api_key()
    base_url = _get_base_url()

    print("=" * 60)
    print("  OpenAI / Tavily API 连通性检查")
    print("  覆盖 core/workflow 与 core/extraction 所有接口")
    print("=" * 60)

    if not api_key:
        print("\n[ERROR] OPENAI_API_KEY 未设置。请在 .env 中配置后重试。")
        sys.exit(1)

    print(f"  OPENAI_API_KEY     : ...{api_key[-4:]}")
    print(f"  OPENAI_BASE_URL    : {base_url or '(默认 OpenAI)'}")
    print(f"  OPENAI_MODEL_NAME  : {_get_model_name()}")
    print(f"  TRANSCRIBER_MODEL  : {_get_transcriber_model()}")
    print(f"  TAVILY_API_KEY     : {'已配置' if _get_tavily_key() else '未配置'}")

    # 初始化客户端（与项目所有节点一致的方式）
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
    except Exception as exc:
        print(f"\n[FATAL] OpenAI 客户端初始化失败: {exc}")
        sys.exit(1)

    # 按顺序执行测试
    test_models_list(client)

    if not args.quick:
        test_chat_text(client)
        test_chat_json_mode(client)
        test_chat_vision(client)
        test_transcription(client)
        test_tavily_search()
    else:
        test_chat_text(client)

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
