"""Video format validation tests.

Coverage:
- validate_video_extension: valid / invalid extensions, edge cases
- validate_video_magic_bytes: valid video files, non-video files, missing files
- InitUploadRequest file_name validator rejection
"""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.schemas.video_format import (
    ALLOWED_VIDEO_EXTENSIONS,
    validate_video_extension,
    validate_video_magic_bytes,
)


# ---------------------------------------------------------------------------
# validate_video_extension
# ---------------------------------------------------------------------------


class TestValidateVideoExtension:
    @pytest.mark.parametrize(
        "ext",
        [".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".mpg", ".mpeg"],
    )
    def test_all_known_extensions_pass(self, ext: str) -> None:
        assert validate_video_extension(f"video{ext}") is True

    @pytest.mark.parametrize("ext", [".MP4", ".Mp4", ".Mkv", ".MOV", ".WEBM"])
    def test_case_insensitive(self, ext: str) -> None:
        assert validate_video_extension(f"video{ext}") is True

    @pytest.mark.parametrize("name", ["video.mp4", "  my_movie.mov  ", "a.avi"])
    def test_whitespace_handling(self, name: str) -> None:
        assert validate_video_extension(name) is True

    @pytest.mark.parametrize(
        "bad_name",
        [
            "malware.exe",
            "doc.pdf",
            "archive.zip",
            "script.py",
            "notes.txt",
            "image.png",
            "video.mp3",
            "data.csv",
        ],
    )
    def test_non_video_extensions_rejected(self, bad_name: str) -> None:
        assert validate_video_extension(bad_name) is False

    def test_empty_string_rejected(self) -> None:
        assert validate_video_extension("") is False

    def test_whitespace_only_rejected(self) -> None:
        assert validate_video_extension("   ") is False

    def test_no_extension_rejected(self) -> None:
        assert validate_video_extension("video_without_ext") is False

    def test_dotfile_rejected(self) -> None:
        # A file like ".hidden" — suffix is ".hidden", not a video ext
        assert validate_video_extension(".hidden") is False


# ---------------------------------------------------------------------------
# validate_video_magic_bytes
# ---------------------------------------------------------------------------


def _write_test_file(header_bytes: bytes, suffix: str = ".bin") -> Path:
    """Helper: write *header_bytes* to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(header_bytes)
    tmp.close()
    return Path(tmp.name)


def _make_mp4_header() -> bytes:
    """Minimal ISO BMFF header (ftyp box)."""
    # 4-byte big-endian size + "ftyp" + "isom" + 4-byte version
    size = struct.pack(">I", 20)
    return size + b"ftypisom\x00\x00\x02\x00"


def _make_avi_header() -> bytes:
    """Minimal AVI RIFF header."""
    # "RIFF" + placeholder size + "AVI "
    riff = b"RIFF" + struct.pack("<I", 0) + b"AVI "
    return riff


def _make_mkv_header() -> bytes:
    """Minimal EBML header for Matroska/WebM."""
    return b"\x1a\x45\xdf\xa3"


def _make_flv_header() -> bytes:
    """Minimal FLV header."""
    return b"FLV\x01"


class TestValidateVideoMagicBytes:
    @pytest.mark.parametrize(
        "label,header",
        [
            ("mp4", _make_mp4_header()),
            ("avi", _make_avi_header()),
            ("mkv", _make_mkv_header()),
            ("webm", _make_mkv_header()),  # same EBML sig
            ("flv", _make_flv_header()),
        ],
    )
    def test_valid_video_headers_pass(self, label: str, header: bytes) -> None:
        path = _write_test_file(header, suffix=f".{label}")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is True, f"Expected valid for {label}, got detected={detected}"
            assert detected is not None
        finally:
            path.unlink(missing_ok=True)

    def test_png_file_rejected(self) -> None:
        # PNG magic: 89 50 4E 47
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        path = _write_test_file(header, suffix=".mp4")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is False
            assert "PNG" in (detected or "")
        finally:
            path.unlink(missing_ok=True)

    def test_pdf_file_rejected(self) -> None:
        header = b"%PDF-1.4\n" + b"\x00" * 100
        path = _write_test_file(header, suffix=".mp4")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is False
            assert "PDF" in (detected or "")
        finally:
            path.unlink(missing_ok=True)

    def test_exe_file_rejected(self) -> None:
        # PE (MZ) header
        header = b"MZ\x90\x00" + b"\x00" * 100
        path = _write_test_file(header, suffix=".mp4")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is False
            assert "executable" in (detected or "").lower()
        finally:
            path.unlink(missing_ok=True)

    def test_zip_file_rejected(self) -> None:
        # ZIP/PK header
        header = b"PK\x03\x04" + b"\x00" * 100
        path = _write_test_file(header, suffix=".mp4")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is False
            assert ("ZIP" in (detected or "")) or ("Office" in (detected or ""))
        finally:
            path.unlink(missing_ok=True)

    def test_missing_file(self) -> None:
        is_valid, detected = validate_video_magic_bytes("/nonexistent/path/file.mp4")
        assert is_valid is False
        assert "not found" in (detected or "")

    def test_empty_file(self) -> None:
        path = _write_test_file(b"", suffix=".mp4")
        try:
            is_valid, detected = validate_video_magic_bytes(str(path))
            assert is_valid is False
            assert "empty" in (detected or "")
        finally:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# InitUploadRequest pydantic validator
# ---------------------------------------------------------------------------


class TestInitUploadRequestFormatValidation:
    """Verify that the pydantic field_validator on file_name rejects
    non-video extensions at the schema layer."""

    def test_valid_video_extension_accepted(self) -> None:
        from backend.schemas.upload import InitUploadRequest

        req = InitUploadRequest(file_name="my_video.mp4", total_size=1024)
        assert req.file_name == "my_video.mp4"

    @pytest.mark.parametrize("bad_name", ["virus.exe", "notes.pdf", "data.zip"])
    def test_non_video_extension_raises_validation_error(self, bad_name: str) -> None:
        from backend.schemas.upload import InitUploadRequest

        with pytest.raises(ValidationError) as exc_info:
            InitUploadRequest(file_name=bad_name, total_size=1024)
        errors = exc_info.value.errors()
        assert any("file_name" in str(e.get("loc", [])) for e in errors)

    def test_no_extension_raises_validation_error(self) -> None:
        from backend.schemas.upload import InitUploadRequest

        with pytest.raises(ValidationError) as exc_info:
            InitUploadRequest(file_name="no_extension", total_size=1024)
        errors = exc_info.value.errors()
        assert any("file_name" in str(e.get("loc", [])) for e in errors)
