from __future__ import annotations

from backend.repositories.device_repository import DeviceRepository
from backend.schemas.device import DeviceRegisterRequest, DeviceRegisterResponse


class DeviceService:
    """Device token business orchestration service."""

    def __init__(self, repository: DeviceRepository) -> None:
        self._repository = repository

    def register_or_update_device(
        self,
        *,
        owner_id: str,
        payload: DeviceRegisterRequest,
    ) -> DeviceRegisterResponse:
        """Register device token or update existing token metadata."""
        existing = self._repository.get_by_token(payload.device_token)
        if existing is not None:
            record = self._repository.update(
                device_token_id=existing.device_token_id,
                user_id=owner_id,
                platform=payload.platform.value,
                app_version=payload.app_version,
                device_id=payload.device_id,
            )
        else:
            record = self._repository.create(
                user_id=owner_id,
                device_token=payload.device_token,
                platform=payload.platform.value,
                app_version=payload.app_version,
                device_id=payload.device_id,
            )

        return DeviceRegisterResponse(
            device_token_id=record.device_token_id,
            platform=payload.platform,
            device_id=record.device_id,
            registered_at=record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def unregister_device(self, *, owner_id: str, device_token_id: str) -> None:
        """Unregister device token for owner."""
        existing = self._repository.get_by_id(device_token_id)
        if existing is None:
            raise LookupError("Device not found")
        if existing.user_id != owner_id:
            raise PermissionError("Cannot unregister another user's device")
        self._repository.delete(device_token_id)

    def list_devices_by_owner(self, *, owner_id: str) -> list[dict[str, str]]:
        """List devices registered by current owner."""
        records = self._repository.list_by_user(owner_id)
        return [
            {
                "device_token_id": record.device_token_id,
                "platform": record.platform,
                "device_id": record.device_id,
                "app_version": record.app_version,
                "registered_at": record.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for record in records
        ]
