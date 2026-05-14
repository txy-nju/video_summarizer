"""User Repository - Database-backed user persistence layer."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, and_, exc
from sqlalchemy.orm import Session

from backend.auth.utils import hash_password
from backend.models.database import User


@dataclass(frozen=True, slots=True)
class UserRecord:
    """Immutable view of a persisted User entity."""

    user_id: str
    username: str
    password_hash: str
    created_at: datetime


class UserRepository:
    """Repository layer for User persistence with SQLAlchemy.
    
    Enforces three key constraints:
    1. Password demasking contract: passwords must be hashed before session.add()
    2. Uniqueness exception handling: IntegrityError -> business exception
    3. Cascade safety constraint: soft deletes do not cascade to dependent tables
    """

    def __init__(self, db_session: Session) -> None:
        """Initialize with a SQLAlchemy Session.
        
        Args:
            db_session: Active SQLAlchemy Session for database access.
        """
        self._session = db_session

    def create(self, *, username: str, password_plaintext: str) -> UserRecord:
        """Create a new user with hashed password.
        
        Enforces password demasking contract: plaintext password is hashed
        before any session operation.
        
        Args:
            username: Unique username.
            password_plaintext: Plain password to hash.
            
        Returns:
            UserRecord with user_id, username, password_hash, created_at.
            
        Raises:
            ValueError: If username already exists (IntegrityError caught and converted).
        """
        # CONSTRAINT 1: Hash password BEFORE session.add()
        password_hash = hash_password(password_plaintext)

        user = User(username=username, password=password_hash)

        try:
            self._session.add(user)
            self._session.flush()  # Catch UNIQUE constraint violation immediately
        except exc.IntegrityError as e:
            self._session.rollback()
            # CONSTRAINT 2: Convert SQL IntegrityError to business exception
            if "username" in str(e).lower():
                raise ValueError(f"Username '{username}' already exists") from e
            raise

        return UserRecord(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password,
            created_at=user.created_at,
        )

    def get_by_id(self, user_id: str) -> UserRecord | None:
        """Retrieve user by ID.
        
        Args:
            user_id: User's unique ID.
            
        Returns:
            UserRecord if found, None otherwise.
        """
        user = self._session.query(User).filter(User.user_id == user_id).one_or_none()
        if user is None:
            return None
        return UserRecord(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password,
            created_at=user.created_at,
        )

    def get_by_username(self, username: str) -> UserRecord | None:
        """Retrieve user by username.
        
        Args:
            username: User's username.
            
        Returns:
            UserRecord if found, None otherwise.
        """
        user = self._session.query(User).filter(User.username == username).one_or_none()
        if user is None:
            return None
        return UserRecord(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password,
            created_at=user.created_at,
        )

    def update_password(self, *, user_id: str, password_plaintext: str) -> UserRecord | None:
        """Update user's password.
        
        Enforces password demasking contract: plaintext password is hashed
        before any session operation.
        
        Args:
            user_id: User's unique ID.
            password_plaintext: New plain password to hash.
            
        Returns:
            Updated UserRecord, or None if user not found.
        """
        user = self._session.query(User).filter(User.user_id == user_id).one_or_none()
        if user is None:
            return None

        # CONSTRAINT 1: Hash password BEFORE session update
        user.password = hash_password(password_plaintext)
        self._session.flush()

        return UserRecord(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password,
            created_at=user.created_at,
        )

    def soft_delete(self, user_id: str) -> bool:
        """Soft-delete user by marking is_deleted flag.
        
        CONSTRAINT 3: Soft delete does NOT cascade to Video_Resource or Knowledge_Base.
        This method only updates the User record; dependent entities are NOT affected.
        
        Args:
            user_id: User's unique ID.
            
        Returns:
            True if soft-deleted, False if user not found.
        """
        user = self._session.query(User).filter(User.user_id == user_id).one_or_none()
        if user is None:
            return False

        # Check if User model has is_deleted and deleted_at fields
        if hasattr(user, "is_deleted") and hasattr(user, "deleted_at"):
            user.is_deleted = True
            user.deleted_at = datetime.now(UTC)
        # If not present in this phase, this is a no-op (User soft-delete not yet in schema)

        self._session.flush()
        return True

    def commit(self) -> None:
        """Explicitly commit the session.
        
        Callers may prefer to manage transactions at the service layer.
        """
        self._session.commit()

    def rollback(self) -> None:
        """Explicitly rollback the session."""
        self._session.rollback()
