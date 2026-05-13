import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt


class TokenError(Exception):
    """Raised when token parsing or validation fails."""


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证明文密码是否与哈希密码匹配。

    该函数解析特定格式的哈希字符串，使用 PBKDF2-HMAC-SHA256 算法重新计算摘要，
    并通过恒定时间比较来防止时序攻击。

    Args:
        plain_password (str): 用户输入的明文密码。
        hashed_password (str): 存储的哈希密码字符串，格式为 "algorithm$rounds$salt_hex$digest_hex"。

    Returns:
        bool: 如果明文密码与哈希密码匹配则返回 True，否则返回 False。
    """
    try:
        algorithm, rounds, salt_hex, digest_hex = hashed_password.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False

    salt = bytes.fromhex(salt_hex)
    expected_digest = bytes.fromhex(digest_hex)
    calculated_digest = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, int(rounds))
    return hmac.compare_digest(calculated_digest, expected_digest)


def create_token(
    *,
    secret_key: str,
    algorithm: str,
    subject: str,
    token_type: str,
    expires_minutes: int,
    extra_claims: Dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    claims: Dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret_key, algorithm=algorithm)


def decode_token(*, token: str, secret_key: str, algorithm: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, secret_key, algorithms=[algorithm])
    except JWTError as exc:
        raise TokenError("Invalid token") from exc
