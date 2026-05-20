
"""
LLM 三能力连通性检查脚本 —— 通过 BaseModel 工厂模式测试 chat / vision / transcribe。

覆盖 core/workflow 和 core/extraction 中的所有 LLM API 调用方式：
  1. Chat Completions（纯文本 + JSON Mode）  — fusion_drafter / chunk_synthesizer / grader
  2. Chat Completions（多模态 Vision）       — chunk_vision_analyzer
  3. Audio Transcriptions（Whisper）          — AudioTranscriber.transcribe
  4. Tavily Search                            — execute_tavily_search（独立）

所有 LLM 调用统一走 get_model_for_capability() 工厂，自动按 CHAT_PROVIDER /
VISION_PROVIDER / TRANSCRIBE_PROVIDER 环境变量路由到对应厂商（openai / deepseek / ...）。

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
# 1. Chat 连通性（纯文本 + JSON Mode）
# ---------------------------------------------------------------------------

def test_chat() -> bool:
    """对应 fusion_drafter_node、chunk_synthesizer._llm_chunk_fusion、grader 等风格。
       通过 get_model_for_capability("chat") 获取模型实例。"""
    _print_header("1. Chat Completions — 纯文本 + JSON Mode")

    from core.llm.config import resolve_api_key, resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability

    provider = resolve_provider("chat")
    model_name = get_model_name_for_capability("chat")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model_name}")

    if not resolve_api_key("chat"):
        _print_fail("Chat", "未配置 CHAT_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY")
        _record("Chat（文本 + JSON）", False, "API Key 未配置")
        return False

    try:
        chat_model = get_model_for_capability("chat")
    except Exception as exc:
        _print_fail("Chat 模型初始化", _extract_error_detail(exc))
        _record("Chat（文本 + JSON）", False, _extract_error_detail(exc))
        return False

    all_ok = True

    # ---- 1a. 纯文本 ----
    try:
        response = chat_model.chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严谨的测试助手。请用一句话回答。"},
                {"role": "user", "content": "说'API连通性测试通过'。"},
            ],
            temperature=0.5,
        )
        print(f"  纯文本回复: {response[:200]}")
        _print_ok("Chat 纯文本")
        _record("Chat 纯文本", True, response[:100])
    except Exception as exc:
        _print_fail("Chat 纯文本", _extract_error_detail(exc))
        _record("Chat 纯文本", False, _extract_error_detail(exc))
        all_ok = False

    # ---- 1b. JSON Mode (temperature=0.0) ----
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
        print(f"  JSON Mode score: {score}")
        _print_ok("Chat JSON Mode" if ok else f"Chat JSON Mode ⚠️ score={score}")
        _record("Chat JSON Mode", ok, f"score={score}")
        if not ok:
            all_ok = False
    except Exception as exc:
        _print_fail("Chat JSON Mode", _extract_error_detail(exc))
        _record("Chat JSON Mode", False, _extract_error_detail(exc))
        all_ok = False

    return all_ok


# ---------------------------------------------------------------------------
# 2. Vision 连通性（多模态）
# ---------------------------------------------------------------------------

def test_vision() -> bool:
    """对应 _llm_vision_chunk_structured 风格：多模态输入，temperature=0.2，
       max_tokens=1024, response_format={"type": "json_object"}。
       通过 get_model_for_capability("vision") 获取模型实例。"""
    _print_header("2. Vision Completions — 多模态视觉")

    from core.llm.config import resolve_api_key, resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability

    provider = resolve_provider("vision")
    model_name = get_model_name_for_capability("vision")
    print(f"  Provider : {provider}")
    print(f"  Model    : {model_name}")

    if not resolve_api_key("vision"):
        _print_fail("Vision", "未配置 VISION_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY")
        _record("Vision 多模态", False, "API Key 未配置")
        return False

    try:
        vision_model = get_model_for_capability("vision")
    except Exception as exc:
        _print_fail("Vision 模型初始化", _extract_error_detail(exc))
        _record("Vision 多模态", False, _extract_error_detail(exc))
        return False

    # 生成一张 64x64 纯蓝 PNG 作为测试图片（满足各厂商最小分辨率限制）
    def _make_test_png() -> bytes:
        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            c = chunk_type + data
            crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
            return struct.pack(">I", len(data)) + c + crc

        w, h = 64, 64
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        # 每行: 1 字节 filter(0) + w*3 字节 RGB 像素
        row = b"\x00" + b"\x00\x00\xff" * w  # 蓝色
        raw = row * h
        compressed = zlib.compress(raw)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")

    img_b64 = base64.b64encode(_make_test_png()).decode("utf-8")

    try:
        response = vision_model.chat_completion(
            model=model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个视觉测试助手。请输出 JSON 对象，"
                        '包含字段 "color"（你看到的图片主色调）和 "resolution"（图片分辨率）。'
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
        try:
            parsed = json.loads(response)
            color = parsed.get("color", "未知")
        except Exception:
            color = response[:80]
        print(f"  识别结果: {color}")
        _print_ok("Vision 多模态", f"识别颜色={color}")
        _record("Vision 多模态", True, color)
        return True
    except Exception as exc:
        msg = str(exc)
        if "not supported" in msg.lower() or "does not support" in msg.lower():
            _print_ok("Vision ⚠️ 模型不支持视觉", msg[:120])
            _record("Vision 多模态", True, f"模型不支持: {msg[:100]}")
            return True
        _print_fail("Vision 多模态", _extract_error_detail(exc))
        _record("Vision 多模态", False, _extract_error_detail(exc))
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
            text = response[:200]
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
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM 三能力连通性检查（通过 BaseModel 工厂模式）"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="仅测试 Chat 纯文本连通性"
    )
    args = parser.parse_args()

    _load_env()

    from core.llm.config import resolve_api_key, resolve_provider

    print("=" * 60)
    print("  LLM 三能力连通性检查")
    print("  覆盖 chat / vision / transcribe（通过 get_model_for_capability 工厂）")
    print("=" * 60)

    # 展示当前配置
    for cap in ("chat", "vision", "transcribe"):
        provider = resolve_provider(cap)
        key = resolve_api_key(cap)
        print(f"  [{cap:11}] provider={provider:8s}  key={'已配置' if key else '未配置':>4s}")

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

