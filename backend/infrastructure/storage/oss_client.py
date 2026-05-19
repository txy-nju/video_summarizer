from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterator

from backend.config import get_settings


class ObjectStorageClient:
    """Object storage adapter with a local filesystem backend.

    This keeps production-facing call sites stable while allowing local testing
    without introducing external SDK dependencies.
    """

    def __init__(self, *, backend: str, local_root: Path, default_ttl_seconds: int) -> None:
        if backend != "local":
            raise ValueError(f"Unsupported storage backend: {backend}")
        self._backend = backend
        self._local_root = local_root
        self._default_ttl_seconds = default_ttl_seconds
        self._local_root.mkdir(parents=True, exist_ok=True)

    def upload_file(self, *, local_path: Path, object_key: str) -> str:
        src = Path(local_path)
        if not src.exists():
            raise FileNotFoundError(f"upload source not found: {src}")
        normalized_key = self._normalize_key(object_key)
        dst = self._local_root / normalized_key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return normalized_key

    @contextmanager
    def materialize_to_local_path(self, object_key: str) -> Iterator[Path]:
        """Yield a local file path for processing tasks.

        For local backend:
        - if object_key already points to an existing local file, use it directly
        - otherwise copy from object storage root to a temporary file
        """
        source = self._resolve_source_path(object_key)
        suffix = source.suffix or ".bin"
        fd, temp_file = tempfile.mkstemp(prefix="oss_obj_", suffix=suffix)
        os.close(fd)
        temp_path = Path(temp_file)
        try:
            shutil.copy2(source, temp_path)
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    def delete_object(self, object_key: str) -> bool:
        target = self._local_root / self._normalize_key(object_key)
        if not target.exists():
            return False
        target.unlink(missing_ok=True)
        return True

    def delete_prefix(self, prefix: str) -> int:
        root = self._local_root / self._normalize_prefix(prefix)
        if not root.exists():
            return 0
        deleted = 0
        if root.is_file():
            root.unlink(missing_ok=True)
            return 1
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)
                deleted += 1
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        root.rmdir()
        return deleted

    def get_presigned_url(self, *, object_key: str, expires_in_seconds: int | None = None) -> str:
        """Return a local development URL-like path for consumers.

        In local mode this is a file path with an expiry hint query parameter.
        """
        ttl = expires_in_seconds or self._default_ttl_seconds
        path = (self._local_root / self._normalize_key(object_key)).resolve().as_posix()
        return f"file://{path}?ttl={ttl}"

    @staticmethod
    def _normalize_key(object_key: str) -> str:
        key = object_key.strip().replace("\\", "/")
        return key.lstrip("/")

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        normalized = prefix.strip().replace("\\", "/").lstrip("/")
        return normalized.rstrip("/")

    def _resolve_source_path(self, object_key: str) -> Path:
        candidate = Path(object_key)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        stored = self._local_root / self._normalize_key(object_key)
        if stored.exists():
            return stored
        raise FileNotFoundError(
            f"Object not found: key={object_key!r}, expected path={stored}"
        )


@lru_cache(maxsize=1)
def get_object_storage_client() -> ObjectStorageClient:
    settings = get_settings()
    root = Path(settings.oss_local_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    return ObjectStorageClient(
        backend=settings.storage_backend,
        local_root=root,
        default_ttl_seconds=settings.oss_presign_ttl_seconds,
    )
