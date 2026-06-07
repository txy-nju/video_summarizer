"""
Video format validation shared module.

Centralises the allowlist of accepted video file extensions and MIME types,
plus magic-byte detection so the system can reject non-video files early
and clean up any partially-allocated resources.

Used by:
- Upload initiation (extension check before creating Redis session)
- Upload finalisation (magic-bytes check after chunk merge)
- Streamlit frontend (server-side extension guard)
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Allowed video extensions (dot-prefixed lowercase)
# ---------------------------------------------------------------------------
ALLOWED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".flv",
        ".wmv",
        ".m4v",
        ".mpg",
        ".mpeg",
    }
)

# ---------------------------------------------------------------------------
# Allowable MIME types (informational — not currently enforced at the
# HTTP layer for TUS uploads because the client doesn't send Content-Type
# for the file until the PATCH chunk phase, but useful for documentation
# and future use).
# ---------------------------------------------------------------------------
ALLOWED_VIDEO_MIME_TYPES: frozenset[str] = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-matroska",
        "video/webm",
        "video/x-flv",
        "video/x-ms-wmv",
        "video/x-m4v",
        "video/mpeg",
    }
)

# ---------------------------------------------------------------------------
# Magic-byte signatures for common video containers.
#
# Each entry maps to a list of (offset, bytes) tuples — ALL must match
# for the format to be positively identified.
# ---------------------------------------------------------------------------
_MAGIC_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    # ISO Base Media File Format (MP4, M4V, MOV, 3GP, etc.)
    # File starts with 4-byte box size (big-endian) + "ftyp" at offset 4.
    ".mp4": [(4, b"ftyp")],
    ".m4v": [(4, b"ftyp")],
    ".mov": [(4, b"ftyp")],  # same container family
    # AVI: "RIFF" header + "AVI " sub-type at offset 8
    ".avi": [(0, b"RIFF"), (8, b"AVI ")],
    # Matroska / WebM: EBML header 0x1A 0x45 0xDF 0xA3
    ".mkv": [(0, b"\x1a\x45\xdf\xa3")],
    ".webm": [(0, b"\x1a\x45\xdf\xa3")],
    # FLV: literal "FLV" followed by version byte
    ".flv": [(0, b"FLV")],
    # WMV / ASF: ASF GUID header
    ".wmv": [(0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11")],
    # MPEG-PS: pack start code 0x00 0x00 0x01 0xBA
    # MPEG-TS: sync byte 0x47 at offset 0 (every 188 bytes, but offset 0 is enough)
    ".mpg": [(0, b"\x00\x00\x01\xba")],
    ".mpeg": [(0, b"\x00\x00\x01\xba")],
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def validate_video_extension(file_name: str) -> bool:
    """Return True if *file_name* ends with a recognised video extension.

    The check is case-insensitive and strips leading/trailing whitespace.
    An empty or extension-less name always returns False.
    """
    if not file_name or not file_name.strip():
        return False
    suffix = Path(file_name.strip()).suffix.lower()
    return suffix in ALLOWED_VIDEO_EXTENSIONS


def validate_video_magic_bytes(file_path: str) -> tuple[bool, str | None]:
    """Inspect the file header to confirm it is a video container.

    Returns:
        ``(True, detected_label)`` when at least one known video signature
        matches.

        ``(False, detected_label)`` when no signature matches.  *detected_label*
        is a human-readable description of what the file *actually* looks like
        (e.g. ``"unknown (starts with 0x504b)"`` or ``"PNG image"``).

    Does **not** raise on I/O errors — a file that cannot be read is treated
    as invalid and returns ``(False, reason_str)``.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return False, "file not found"
        if path.stat().st_size == 0:
            return False, "empty file"

        # Read enough to match all known signatures (max offset + max header len)
        read_size = max(
            offset + len(sig)
            for sigs in _MAGIC_SIGNATURES.values()
            for offset, sig in sigs
        )
        with open(file_path, "rb") as fh:
            header = fh.read(read_size)

        # If the file is shorter than the largest signature we check, the
        # slice ``header[offset:offset+len(sig)]`` will be shorter than
        # ``sig`` and the equality check naturally fails — no special case
        # needed.
        if len(header) < 4:
            return False, "file too small to identify"

        for ext, checks in _MAGIC_SIGNATURES.items():
            if all(header[offset : offset + len(sig)] == sig for offset, sig in checks):
                return True, ext.lstrip(".")

        # Attempt a readable label for common non-video types
        readable = _guess_non_video_type(header)
        return False, readable

    except OSError as exc:
        return False, f"cannot read file: {exc}"


def _guess_non_video_type(header: bytes) -> str:
    """Return a human label for a few well-known non-video signatures."""
    if len(header) < 4:
        return "unknown (too small)"
    if header[:4] == b"\x89PNG":
        return "PNG image"
    if header[:3] == b"\xff\xd8\xff":
        return "JPEG image"
    if header[:4] == b"GIF8":
        return "GIF image"
    if header[:2] == b"BM":
        return "BMP image"
    if header[:4] == b"%PDF":
        return "PDF document"
    if header[:2] == b"PK":
        if len(header) >= 30 and b"word/" in header:
            return "Microsoft Office document (DOCX/XLSX/PPTX)"
        return "ZIP archive or Office document"
    if header[:4] == b"MZ\x90\x00" or header[:2] == b"MZ":
        return "Windows executable (PE)"
    if header[:4] == b"\x7fELF":
        return "Linux executable (ELF)"
    if header[:4] == b"RIFF":
        return f"RIFF container (sub-type: {header[8:12].decode(errors='replace')})"
    return f"unknown (starts with {header[:4].hex()})"
