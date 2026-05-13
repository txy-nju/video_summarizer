from dataclasses import dataclass
from threading import Lock
from typing import Dict
from uuid import uuid4

from fastapi import HTTPException, status

from backend.auth.models import TokenResponseData, UserView
from backend.auth.utils import create_token, hash_password, verify_password
from backend.config import Settings


@dataclass
class StoredUser:
    user_id: str
    username: str
    password_hash: str


class AuthService:
    """Minimal auth orchestration service for phase-2 bootstrap."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._users_by_name: Dict[str, StoredUser] = {}
        self._users_by_id: Dict[str, StoredUser] = {}
        self._lock = Lock()

    def register_user(self, username: str, password: str) -> UserView:
        with self._lock:
            if username in self._users_by_name:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
            user = StoredUser(user_id=str(uuid4()), username=username, password_hash=hash_password(password))
            self._users_by_name[username] = user
            self._users_by_id[user.user_id] = user
        return UserView(user_id=user.user_id, username=user.username)

    def authenticate_user(self, username: str, password: str, device_id: str) -> TokenResponseData:
        user = self._users_by_name.get(username)
        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        return self._issue_token_pair(user=user, device_id=device_id)

    def refresh_access_token(self, user_id: str, username: str, device_id: str) -> TokenResponseData:
        user = self._users_by_id.get(user_id)
        if user is None or user.username != username:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token subject")
        return self._issue_token_pair(user=user, device_id=device_id)

    def get_user_by_id(self, user_id: str) -> UserView:
        user = self._users_by_id.get(user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        return UserView(user_id=user.user_id, username=user.username)

    def _issue_token_pair(self, *, user: StoredUser, device_id: str) -> TokenResponseData:
        access_token = create_token(
            secret_key=self._settings.jwt_secret_key,
            algorithm=self._settings.jwt_algorithm,
            subject=user.user_id,
            token_type="access",
            expires_minutes=self._settings.jwt_access_token_expires_minutes,
            extra_claims={"username": user.username},
        )
        refresh_token = create_token(
            secret_key=self._settings.jwt_refresh_secret_key,
            algorithm=self._settings.jwt_algorithm,
            subject=user.user_id,
            token_type="refresh",
            expires_minutes=self._settings.jwt_refresh_token_expires_minutes,
            extra_claims={"username": user.username, "device_id": device_id},
        )
        return TokenResponseData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.jwt_access_token_expires_minutes * 60,
            user=UserView(user_id=user.user_id, username=user.username),
        )
