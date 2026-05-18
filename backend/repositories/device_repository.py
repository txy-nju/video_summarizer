"""
设备标识与 FCM 令牌持久化。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.models.database import DeviceToken


@dataclass(frozen=True, slots=True)
class DeviceTokenRecord:
    device_token_id: str
    user_id: str
    device_token: str
    platform: str
    app_version: str | None
    device_id: str
    created_at: datetime


class DeviceRepository:
    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(self, *, user_id: str, device_token: str, platform: str, app_version: str, device_id: str) -> DeviceTokenRecord:
        entity = DeviceToken(
            user_id=user_id,
            device_token=device_token,
            platform=platform,
            app_version=app_version,
            device_id=device_id,
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity)

    def update(self, *, device_token_id: str, user_id: str, platform: str, app_version: str, device_id: str) -> DeviceTokenRecord:
        entity = self._session.query(DeviceToken).filter(DeviceToken.device_token_id == device_token_id).one_or_none()
        if entity is None:
            raise ValueError(f"Device token not found: {device_token_id}")
        entity.user_id = user_id
        entity.platform = platform
        entity.app_version = app_version
        entity.device_id = device_id
        self._session.commit()
        return self._to_record(entity)

    def get_by_token(self, device_token: str) -> DeviceTokenRecord | None:
        entity = self._session.query(DeviceToken).filter(DeviceToken.device_token == device_token).one_or_none()
        if entity is None:
            return None
        return self._to_record(entity)

    def get_by_id(self, device_token_id: str) -> DeviceTokenRecord | None:
        entity = self._session.query(DeviceToken).filter(DeviceToken.device_token_id == device_token_id).one_or_none()
        if entity is None:
            return None
        return self._to_record(entity)

    def list_by_user(self, user_id: str) -> list[DeviceTokenRecord]:
        entities = self._session.query(DeviceToken).filter(DeviceToken.user_id == user_id).all()
        return [self._to_record(e) for e in entities]

    def delete(self, device_token_id: str) -> bool:
        entity = self._session.query(DeviceToken).filter(DeviceToken.device_token_id == device_token_id).one_or_none()
        if entity is None:
            return False
        self._session.delete(entity)
        self._session.commit()
        return True

    @staticmethod
    def _to_record(entity: DeviceToken) -> DeviceTokenRecord:
        return DeviceTokenRecord(
            device_token_id=entity.device_token_id,
            user_id=entity.user_id,
            device_token=entity.device_token,
            platform=entity.platform,
            app_version=entity.app_version,
            device_id=entity.device_id,
            created_at=entity.created_at,
        )
