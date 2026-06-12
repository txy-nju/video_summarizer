"""
Integration verification: AIGC transcription full flow (13.1-13.5)

Verifies:
1. Factory routing selects AigcModel correctly
2. AigcModel instantiation (API key)
3. Capability declarations
4. lasr public params construction
5. TranscriptionResult AIGC format conversion
6. OpenAI fallback compatibility
7. DeepSeek/Gemini correctly decline transcribe

Usage:
    python tests/core/llm/test_aigc_integration.py
"""

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Load .env
from dotenv import load_dotenv
env_path = _project_root / ".env"
load_dotenv(dotenv_path=env_path)

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


def check_env():
    """Check required environment variables."""
    print("=" * 60)
    print("1. Environment Variable Check")
    print("=" * 60)

    provider = os.getenv("TRANSCRIBE_PROVIDER", "")
    api_key = os.getenv("TRANSCRIBE_API_KEY") or os.getenv("AIGC_API_KEY", "")

    print(f"  TRANSCRIBE_PROVIDER = {provider or '(not set)'}")
    print(f"  TRANSCRIBE_API_KEY  = {'***set***' if api_key else '(not set)'}")

    errors = []
    if provider != "aigc":
        errors.append(f"TRANSCRIBE_PROVIDER should be 'aigc', currently '{provider}'")
    if not api_key:
        errors.append("TRANSCRIBE_API_KEY or AIGC_API_KEY not set")

    if errors:
        print(f"\n  {FAIL} Configuration errors:")
        for e in errors:
            print(f"     - {e}")
        return False

    print(f"  {PASS} Environment configured correctly")
    return True


def check_factory_routing():
    """Verify factory routes to correct provider."""
    print("\n" + "=" * 60)
    print("2. Factory Routing")
    print("=" * 60)

    from core.llm.config import resolve_provider
    from core.llm.factory import get_model_for_capability, get_model_name_for_capability
    from core.llm.aigc_model import AigcModel

    provider = resolve_provider("transcribe")
    print(f"  resolve_provider('transcribe') = {provider}")
    assert provider == "aigc", f"Expected 'aigc', got '{provider}'"
    print(f"  {PASS} Provider resolved correctly")

    model_name = get_model_name_for_capability("transcribe")
    print(f"  get_model_name_for_capability('transcribe') = {model_name}")

    # If TRANSCRIBE_MODEL_NAME is explicitly set in .env, it takes precedence
    # over provider default (correct behavior). Otherwise, should be fileasrrecorder.
    explicit_model = os.getenv("TRANSCRIBE_MODEL_NAME", "").strip() or os.getenv("TRANSCRIBER_MODEL", "").strip()
    if explicit_model:
        print(f"  (explicitly set via env, skipping default check)")
    else:
        assert model_name == "fileasrrecorder", \
            f"Expected default 'fileasrrecorder' for aigc, got '{model_name}'"
    print(f"  {PASS} Model name resolved: {model_name}")

    model = get_model_for_capability("transcribe")
    assert isinstance(model, AigcModel), f"Expected AigcModel, got {type(model).__name__}"
    print(f"  get_model_for_capability('transcribe') -> {type(model).__name__}")
    print(f"  {PASS} Provider instantiated successfully")


def check_capability_declarations():
    """Verify capability declaration attributes."""
    print("\n" + "=" * 60)
    print("3. Capability Declarations")
    print("=" * 60)

    from core.llm.factory import get_model_for_capability

    model = get_model_for_capability("transcribe")

    assert model.supports_transcribe is True
    print(f"  supports_transcribe       = {model.supports_transcribe} {PASS}")

    expected_max = 500 * 1024 * 1024
    assert model.max_audio_upload_bytes == expected_max, \
        f"Expected {expected_max}, got {model.max_audio_upload_bytes}"
    print(f"  max_audio_upload_bytes    = {model.max_audio_upload_bytes / 1024 / 1024:.0f}MB {PASS}")

    expected_chunk = 5 * 1024 * 1024
    assert model.audio_chunk_size_bytes == expected_chunk
    print(f"  audio_chunk_size_bytes    = {model.audio_chunk_size_bytes / 1024 / 1024:.0f}MB {PASS}")


def check_lasr_public_params():
    """Verify lasr public params construction."""
    print("\n" + "=" * 60)
    print("4. lasr Public Parameters")
    print("=" * 60)

    from core.llm.factory import get_model_for_capability

    model = get_model_for_capability("transcribe")
    params = model._build_lasr_public_params("fileasrrecorder")

    required_keys = ["client_version", "package", "user_id", "system_time", "engineid", "requestId"]
    for key in required_keys:
        assert key in params, f"Missing parameter: {key}"
        assert params[key], f"Parameter {key} is empty"

    print(f"  client_version = {params['client_version']}")
    print(f"  package        = {params['package']}")
    print(f"  user_id        = {params['user_id'][:8]}... ({len(params['user_id'])} chars)")
    print(f"  system_time    = {params['system_time']} (ms)")
    print(f"  engineid       = {params['engineid']}")
    print(f"  requestId      = {params['requestId'][:8]}...")

    # user_id must be 32 chars
    assert len(params["user_id"]) == 32, f"user_id length should be 32, got {len(params['user_id'])}"
    # user_id can only contain lowercase letters and digits
    import re
    assert re.match(r'^[a-z0-9]{32}$', params["user_id"]), \
        "user_id format invalid (need 32 lowercase letters + digits)"

    print(f"  {PASS} All public parameters valid")


def check_transcription_result_format():
    """Verify TranscriptionResult.from_aigc_lasr_response format conversion."""
    print("\n" + "=" * 60)
    print("5. TranscriptionResult AIGC Format Conversion")
    print("=" * 60)

    from core.llm.transcription_result import TranscriptionResult

    # Mock vivo API response
    mock_response = {
        "result": [
            {"onebest": "Hello everyone, welcome to today's program.", "bg": 0, "ed": 3200, "speaker": 1},
            {"onebest": "Today we discuss AI.", "bg": 3500, "ed": 7800, "speaker": 1},
            {"onebest": "AI is changing the world.", "bg": 8000, "ed": 12500, "speaker": 2},
        ]
    }

    result = TranscriptionResult.from_aigc_lasr_response(mock_response)

    # Verify conversion
    assert len(result.segments) == 3, f"Expected 3 segments, got {len(result.segments)}"
    assert result.language == "", f"Expected language='', got '{result.language}'"
    assert result.duration == 12.5, f"Expected duration=12.5, got {result.duration}"

    # Verify ms->s conversion
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 3.2
    assert result.segments[1].start == 3.5
    assert result.segments[1].end == 7.8
    assert result.segments[2].start == 8.0
    assert result.segments[2].end == 12.5

    # Verify to_json compatibility
    json_str = result.to_json()
    import json
    parsed = json.loads(json_str)
    assert "segments" in parsed
    assert parsed["segments"][0]["start"] == 0.0
    assert parsed["language"] == ""

    print(f"  segments count: {len(result.segments)}")
    print(f"  text preview: {result.text[:60]}...")
    print(f"  duration: {result.duration}s")
    print(f"  language: '{result.language}' (AIGC returns none, default='')")
    print(f"  {PASS} AIGC format conversion correct, to_json compatible with Whisper")


def check_openai_fallback():
    """Verify OpenAI fallback path still works (13.2)."""
    print("\n" + "=" * 60)
    print("6. OpenAI Fallback Compatibility")
    print("=" * 60)

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print(f"  {SKIP} OPENAI_API_KEY not set, skipping fallback test")
        return

    from core.llm.openai_model import OpenAIModel

    model = OpenAIModel(api_key=openai_key)
    assert model.supports_transcribe is True
    assert model.max_audio_upload_bytes == 25 * 1024 * 1024
    assert model.audio_chunk_size_bytes is None
    print(f"  OpenAIModel instantiated successfully")
    print(f"  supports_transcribe = {model.supports_transcribe}")
    print(f"  {PASS} OpenAI fallback path OK")


def check_deepseek_gemini_no_transcribe():
    """Verify DeepSeek/Gemini correctly declare no transcribe support."""
    print("\n" + "=" * 60)
    print("7. Unsupported Provider Verification")
    print("=" * 60)

    from core.llm.deepseek_model import DeepSeekModel
    from core.llm.gemini_model import GeminiModel

    ds = DeepSeekModel(api_key="test")
    assert ds.supports_transcribe is False
    print(f"  DeepSeekModel.supports_transcribe = False {PASS}")

    gm = GeminiModel(api_key="test")
    assert gm.supports_transcribe is False
    print(f"  GeminiModel.supports_transcribe = False {PASS}")


def check_audio_transcriber_integration():
    """Verify AudioTranscriber works with AIGC provider (no actual API call)."""
    print("\n" + "=" * 60)
    print("8. AudioTranscriber + AIGC Integration")
    print("=" * 60)

    from core.extraction.infrastructure.transcriber import AudioTranscriber
    from core.llm.aigc_model import AigcModel

    transcriber = AudioTranscriber()
    assert isinstance(transcriber.transcribe_model, AigcModel), \
        f"Expected AigcModel, got {type(transcriber.transcribe_model).__name__}"
    assert transcriber._provider_label == "aigc"
    print(f"  transcribe_model type: {type(transcriber.transcribe_model).__name__} {PASS}")
    print(f"  provider_label: {transcriber._provider_label} {PASS}")


def check_split_audio_with_aigc_limits():
    """Verify _split_audio respects AIGC's 500MB limit (13.5)."""
    print("\n" + "=" * 60)
    print("9. Large File Splitting with AIGC Limits")
    print("=" * 60)

    from core.extraction.infrastructure.transcriber import _split_audio

    # Create a small test file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b'\x00' * (10 * 1024 * 1024))  # 10MB file
        tmp_path = Path(f.name)

    try:
        # With AIGC's 500MB limit, 10MB file should NOT be split
        segments = _split_audio(tmp_path, max_bytes=500 * 1024 * 1024)
        assert len(segments) == 1, f"10MB file should not be split under 500MB limit"
        print(f"  10MB file with 500MB limit: {len(segments)} segment(s) {PASS}")

        # With None limit, should not split
        segments_none = _split_audio(tmp_path, max_bytes=None)
        assert len(segments_none) == 1
        print(f"  10MB file with None limit: {len(segments_none)} segment(s) {PASS}")

    finally:
        tmp_path.unlink(missing_ok=True)


def main():
    """Run all integration checks."""
    print("\n" + "=" * 60)
    print("  AIGC Transcription Integration Verification")
    print("=" * 60)

    all_ok = True

    checks = [
        ("Environment", check_env),
        ("Factory Routing", check_factory_routing),
        ("Capability Declarations", check_capability_declarations),
        ("lasr Parameters", check_lasr_public_params),
        ("Format Conversion", check_transcription_result_format),
        ("OpenAI Fallback", check_openai_fallback),
        ("Unsupported Providers", check_deepseek_gemini_no_transcribe),
        ("AudioTranscriber + AIGC", check_audio_transcriber_integration),
        ("File Splitting", check_split_audio_with_aigc_limits),
    ]

    for name, check_fn in checks:
        try:
            check_fn()
        except Exception as e:
            print(f"\n  {FAIL} Failed: {e}")
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print(f"{PASS} All integration verifications passed!")
    else:
        print(f"{FAIL} Some verifications failed, check errors above.")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
