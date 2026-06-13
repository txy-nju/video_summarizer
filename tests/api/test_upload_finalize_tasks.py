from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.db.session import SessionLocal
from backend.models.database import User, VideoResource
from backend.tasks.upload_finalize_tasks import async_finalize_upload


@dataclass
class _FakeUploadService:
    owner_id: str
    file_name: str
    merged_path: str

    def finalize_upload(self, *, upload_id: str) -> dict:
        return {
            "upload_id": upload_id,
            "status": "MERGED",
            "owner_id": self.owner_id,
            "file_name": self.file_name,
            "merged_path": self.merged_path,
        }


class _FakeStorageClient:
    def __init__(self) -> None:
        self.last_uploaded_key: str | None = None

    def upload_file(self, *, local_path: Path, object_key: str) -> str:
        assert local_path.exists()
        self.last_uploaded_key = object_key
        return object_key


def test_build_video_object_key_uses_object_key_contract() -> None:
    from backend.tasks.upload_finalize_tasks import _build_video_object_key

    key = _build_video_object_key(
        owner_id="usr_001",
        video_id="vid_001",
        file_name="Demo.MP4",
        merged_path="/tmp/ignored.bin",
    )

    assert key == "videos/usr_001/vid_001/original.mp4"


def test_async_finalize_upload_writes_object_key_in_db(monkeypatch, tmp_path) -> None:
    upload_id = "upl_001"
    owner_id = "user-upload-finalize"
    video_id = "video-upload-finalize"

    merged_file = tmp_path / "merged.mp4"
    merged_file.write_bytes(b"video-bytes")

    with SessionLocal() as db:
        db.add(User(user_id=owner_id, username="upload_finalize_u", password="hashed"))
        db.add(
            VideoResource(
                video_id=video_id,
                owner_id=owner_id,
                file_name="demo.mp4",
                oss_key=None,
            )
        )
        db.commit()

    fake_upload_service = _FakeUploadService(
        owner_id=owner_id,
        file_name="Demo.MP4",
        merged_path=str(merged_file),
    )
    fake_storage_client = _FakeStorageClient()
    published: dict[str, str] = {}

    monkeypatch.setattr(
        "backend.tasks.upload_finalize_tasks._create_upload_service",
        lambda: fake_upload_service,
    )
    # Bypass magic-byte validation — test file is not a real video container
    monkeypatch.setattr(
        "backend.tasks.upload_finalize_tasks.validate_video_magic_bytes",
        lambda file_path: (True, "mp4"),
    )
    _expected_video_id = video_id
    monkeypatch.setattr(
        "backend.tasks.upload_finalize_tasks._create_video_resource",
        lambda *, owner_id, file_name: _expected_video_id,
    )
    # Retry idempotency: return None so a new record is created on first attempt
    monkeypatch.setattr(
        "backend.tasks.upload_finalize_tasks._get_session_video_id",
        lambda upload_id: None,
    )
    monkeypatch.setattr(
        "backend.infrastructure.storage.oss_client.get_object_storage_client",
        lambda: fake_storage_client,
    )
    monkeypatch.setattr(
        "backend.tasks.upload_finalize_tasks._publish_video_uploaded_event",
        lambda *, video_id, owner_id, oss_key, trace_id="": published.update({"oss_key": oss_key}),
    )

    class _FakeUploadRepo:
        def __init__(self, redis_client) -> None:
            self.redis_client = redis_client

        def cleanup_chunks(self, _upload_id: str) -> None:
            return None

        def finalize_session(self, upload_id: str, *, video_id=None, final_state="done") -> None:
            return None

        def set_video_id(self, upload_id: str, video_id: str):
            return None

        def get_session(self, upload_id: str):
            return None  # Always return None → no pre-existing video_id in session

    monkeypatch.setattr("backend.repositories.upload_repository.UploadRepository", _FakeUploadRepo)
    monkeypatch.setattr("redis.Redis.from_url", lambda *args, **kwargs: object())

    result = async_finalize_upload.run(upload_id)

    expected_key = f"videos/{owner_id}/{video_id}/original.mp4"
    assert result["status"] == "DONE"
    assert result["oss_key"] == expected_key
    assert fake_storage_client.last_uploaded_key == expected_key
    assert published["oss_key"] == expected_key
    assert ":" not in result["oss_key"]

    with SessionLocal() as db:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one()
        assert row.oss_key == expected_key
