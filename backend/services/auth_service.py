"""AuthService - JWT authentication orchestration with database persistence."""

from backend.auth.models import TokenResponseData, UserView
from backend.auth.utils import create_token, verify_password
from backend.config import Settings
from backend.exceptions import AuthError, ConflictError, ErrorCode, NotFoundError
from backend.repositories.user_repository import UserRepository


class AuthService:
    """Authentication orchestration service with database-backed user persistence.

    Coordinates:
    - User registration and login via UserRepository
    - JWT token generation and refresh
    - Password verification
    """

    def __init__(
        self,
        user_repository: UserRepository,
        settings: Settings,
    ):
        """Initialize with UserRepository and Settings.

        Args:
            user_repository: Persistence layer for user data.
            settings: Application configuration.
        """
        self._user_repository = user_repository
        self._settings = settings

    def register_user(self, username: str, password: str) -> UserView:
        """Register a new user.

        Args:
            username: Unique username.
            password: Plain password (will be hashed by repository).

        Returns:
            UserView with registered user_id and username.

        Raises:
            HTTPException 409: If username already exists.
        """
        try:
            user_record = self._user_repository.create(username=username, password_plaintext=password)
            self._user_repository.commit()
        except ValueError as e:
            # Repository layer raises ValueError on duplicate username
            raise ConflictError(code=ErrorCode.AUTH_USERNAME_ALREADY_EXISTS, message=str(e)) from e

        return UserView(user_id=str(user_record.user_id), username=user_record.username)

    def authenticate_user(self, username: str, password: str, device_id: str) -> TokenResponseData:
        """Authenticate user and issue token pair.

        Args:
            username: User's username.
            password: Plain password to verify.
            device_id: Device identifier for token binding.

        Returns:
            TokenResponseData with access_token, refresh_token, and user info.

        Raises:
            HTTPException 401: If user not found or password incorrect.
        """
        user_record = self._user_repository.get_by_username(username)
        if user_record is None or not verify_password(password, user_record.password_hash):
            raise AuthError(code=ErrorCode.AUTH_INVALID_CREDENTIALS, message="Invalid credentials")

        return self._issue_token_pair(user_id=user_record.user_id, username=user_record.username, device_id=device_id)

    def refresh_access_token(self, user_id: str, username: str, device_id: str) -> TokenResponseData:
        """Refresh access token using refresh token claims.

        Validates that the user exists and username matches before issuing new tokens.

        Args:
            user_id: User's unique ID from refresh token.
            username: User's username from refresh token.
            device_id: Device ID from refresh token.

        Returns:
            TokenResponseData with new token pair.

        Raises:
            HTTPException 401: If user not found or username mismatch.
        """
        user_record = self._user_repository.get_by_id(user_id)
        if user_record is None or user_record.username != username:
            raise AuthError(code=ErrorCode.AUTH_INVALID_TOKEN, message="Invalid refresh token subject")

        return self._issue_token_pair(user_id=user_record.user_id, username=user_record.username, device_id=device_id)

    def get_user_by_id(self, user_id: str) -> UserView:
        """Retrieve user by ID.

        Args:
            user_id: User's unique ID.

        Returns:
            UserView with user_id and username.

        Raises:
            HTTPException 404: If user not found.
        """
        user_record = self._user_repository.get_by_id(user_id)
        if user_record is None:
            raise NotFoundError(code=ErrorCode.AUTH_USER_NOT_FOUND, message="User not found")

        return UserView(user_id=str(user_record.user_id), username=user_record.username)

    def _issue_token_pair(self, *, user_id: str, username: str, device_id: str) -> TokenResponseData:
        """Internal: Issue access and refresh token pair.

        Args:
            user_id: User's unique ID.
            username: User's username.
            device_id: Device identifier for token binding.

        Returns:
            TokenResponseData with both tokens.
        """
        access_token = create_token(
            secret_key=self._settings.jwt_secret_key,
            algorithm=self._settings.jwt_algorithm,
            subject=str(user_id),
            token_type="access",
            expires_minutes=self._settings.jwt_access_token_expires_minutes,
            extra_claims={"username": username},
        )
        refresh_token = create_token(
            secret_key=self._settings.jwt_refresh_secret_key,
            algorithm=self._settings.jwt_algorithm,
            subject=str(user_id),
            token_type="refresh",
            expires_minutes=self._settings.jwt_refresh_token_expires_minutes,
            extra_claims={"username": username, "device_id": device_id},
        )

        return TokenResponseData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.jwt_access_token_expires_minutes * 60,
            user=UserView(user_id=str(user_id), username=username),
        )
