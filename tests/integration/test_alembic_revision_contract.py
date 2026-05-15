from __future__ import annotations

from pathlib import Path
import re


VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"
REVISION_PATTERN = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
DOWN_REVISION_PATTERN = re.compile(r'^down_revision\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_alembic_revision_id_length_within_postgres_default() -> None:
    """Alembic default alembic_version.version_num is VARCHAR(32) on PostgreSQL."""
    version_files = sorted(VERSIONS_DIR.glob("*.py"))
    assert version_files, "alembic/versions should contain migration files"

    for path in version_files:
        text = _read_text(path)
        match = REVISION_PATTERN.search(text)
        assert match is not None, f"revision not found in {path.name}"
        revision = match.group(1)
        assert len(revision) <= 32, f"revision too long (>32): {path.name} -> {revision}"


def test_alembic_revision_chain_uniqueness() -> None:
    """Basic contract: migration revision and down_revision should not self-loop."""
    version_files = sorted(VERSIONS_DIR.glob("*.py"))
    revisions: set[str] = set()

    for path in version_files:
        text = _read_text(path)
        revision_match = REVISION_PATTERN.search(text)
        assert revision_match is not None, f"revision not found in {path.name}"
        revision = revision_match.group(1)
        assert revision not in revisions, f"duplicate revision id: {revision}"
        revisions.add(revision)

        down_match = DOWN_REVISION_PATTERN.search(text)
        if down_match is not None:
            down_revision = down_match.group(1)
            assert down_revision != revision, f"self-referencing down_revision in {path.name}"
