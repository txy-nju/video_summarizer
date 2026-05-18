"""DeviceRepository 单元测试。

覆盖：
- 创建/更新/查询/删除设备令牌
- 唯一性约束（device_token 不可重复为不同 user 注册）
- 多用户隔离（list_by_user 不泄漏）
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.db.session import SessionLocal
from backend.models.database import DeviceToken
from backend.repositories.device_repository import DeviceRepository


@pytest.fixture
def repo() -> DeviceRepository:
    db = SessionLocal()
    from backend.models.database import User
    from backend.auth.utils import hash_password
    # Ensure test users exist for FK constraints
    for uid, uname in [("usr_001", "testuser1"), ("usr_alice", "alice"), ("usr_bob", "bob")]:
        existing = db.query(User).filter(User.user_id == uid).one_or_none()
        if existing is None:
            db.add(User(user_id=uid, username=uname, password=hash_password("Secret123!")))
    db.commit()
    return DeviceRepository(db_session=db)


def _count_device_tokens(session) -> int:
    return session.execute(text("SELECT COUNT(*) FROM device_tokens")).scalar_one()


class TestDeviceRepositoryCreate:
    def test_create_device_token(self, repo: DeviceRepository) -> None:
        record = repo.create(
            user_id="usr_001",
            device_token="fcm_token_abc",
            platform="android",
            app_version="1.0.0",
            device_id="android_001",
        )
        assert record.device_token_id
        assert record.user_id == "usr_001"
        assert record.device_token == "fcm_token_abc"
        assert record.platform == "android"
        assert record.app_version == "1.0.0"
        assert record.device_id == "android_001"
        assert record.created_at

        assert _count_device_tokens(repo._session) == 1

    def test_create_multiple_devices_same_user(self, repo: DeviceRepository) -> None:
        r1 = repo.create(
            user_id="usr_001",
            device_token="fcm_token_1",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        r2 = repo.create(
            user_id="usr_001",
            device_token="fcm_token_2",
            platform="android",
            app_version="1.0.0",
            device_id="tablet",
        )
        assert r1.device_token_id != r2.device_token_id
        assert r1.user_id == r2.user_id == "usr_001"
        assert _count_device_tokens(repo._session) == 2

    def test_create_duplicate_token_same_user(self, repo: DeviceRepository) -> None:
        repo.create(
            user_id="usr_001",
            device_token="fcm_token_dup",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        with pytest.raises(Exception):  # IntegrityError due to unique constraint
            repo.create(
                user_id="usr_001",
                device_token="fcm_token_dup",
                platform="android",
                app_version="1.0.0",
                device_id="phone",
            )
        repo._session.rollback()

    def test_create_different_users(self, repo: DeviceRepository) -> None:
        repo.create(
            user_id="usr_alice",
            device_token="alice_token",
            platform="ios",
            app_version="2.0.0",
            device_id="iphone_alice",
        )
        repo.create(
            user_id="usr_bob",
            device_token="bob_token",
            platform="android",
            app_version="1.5.0",
            device_id="android_bob",
        )
        assert _count_device_tokens(repo._session) == 2


class TestDeviceRepositoryQuery:
    def test_get_by_token_found(self, repo: DeviceRepository) -> None:
        repo.create(
            user_id="usr_001",
            device_token="fcm_token_find",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        record = repo.get_by_token("fcm_token_find")
        assert record is not None
        assert record.device_token == "fcm_token_find"
        assert record.user_id == "usr_001"

    def test_get_by_token_not_found(self, repo: DeviceRepository) -> None:
        record = repo.get_by_token("nonexistent_token")
        assert record is None

    def test_get_by_id_found(self, repo: DeviceRepository) -> None:
        r = repo.create(
            user_id="usr_001",
            device_token="fcm_token_by_id",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        record = repo.get_by_id(r.device_token_id)
        assert record is not None
        assert record.device_token_id == r.device_token_id

    def test_get_by_id_not_found(self, repo: DeviceRepository) -> None:
        record = repo.get_by_id("nonexistent_id")
        assert record is None

    def test_list_by_user_isolation(self, repo: DeviceRepository) -> None:
        repo.create(
            user_id="usr_alice",
            device_token="alice_t1",
            platform="android",
            app_version="1.0.0",
            device_id="alice_phone",
        )
        repo.create(
            user_id="usr_alice",
            device_token="alice_t2",
            platform="android",
            app_version="1.0.0",
            device_id="alice_tablet",
        )
        repo.create(
            user_id="usr_bob",
            device_token="bob_t1",
            platform="ios",
            app_version="2.0.0",
            device_id="bob_phone",
        )

        alice_records = repo.list_by_user("usr_alice")
        assert len(alice_records) == 2
        assert {r.device_token for r in alice_records} == {"alice_t1", "alice_t2"}

        bob_records = repo.list_by_user("usr_bob")
        assert len(bob_records) == 1
        assert bob_records[0].device_token == "bob_t1"

    def test_list_by_user_empty(self, repo: DeviceRepository) -> None:
        records = repo.list_by_user("no_device_user")
        assert records == []


class TestDeviceRepositoryUpdate:
    def test_update_device_info(self, repo: DeviceRepository) -> None:
        r = repo.create(
            user_id="usr_001",
            device_token="fcm_token_update",
            platform="android",
            app_version="1.0.0",
            device_id="old_phone",
        )
        updated = repo.update(
            device_token_id=r.device_token_id,
            user_id="usr_001",
            platform="ios",
            app_version="2.0.0",
            device_id="new_phone",
        )
        assert updated.platform == "ios"
        assert updated.app_version == "2.0.0"
        assert updated.device_id == "new_phone"
        assert updated.user_id == "usr_001"

    def test_update_nonexistent_raises(self, repo: DeviceRepository) -> None:
        with pytest.raises(ValueError, match="Device token not found"):
            repo.update(
                device_token_id="nonexistent",
                user_id="usr_001",
                platform="android",
                app_version="1.0.0",
                device_id="phone",
            )


class TestDeviceRepositoryDelete:
    def test_delete_existing(self, repo: DeviceRepository) -> None:
        r = repo.create(
            user_id="usr_001",
            device_token="fcm_token_del",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        assert _count_device_tokens(repo._session) == 1
        result = repo.delete(r.device_token_id)
        assert result is True
        assert _count_device_tokens(repo._session) == 0
        assert repo.get_by_id(r.device_token_id) is None

    def test_delete_nonexistent(self, repo: DeviceRepository) -> None:
        result = repo.delete("nonexistent_id")
        assert result is False

    def test_delete_then_get_returns_none(self, repo: DeviceRepository) -> None:
        r = repo.create(
            user_id="usr_001",
            device_token="fcm_token_del2",
            platform="android",
            app_version="1.0.0",
            device_id="phone",
        )
        repo.delete(r.device_token_id)
        assert repo.get_by_token("fcm_token_del2") is None
        assert repo.get_by_id(r.device_token_id) is None
