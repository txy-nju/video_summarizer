from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth.utils import TokenError, decode_token
from backend.config import Settings
from backend.dependencies import get_app_settings, get_auth_service
from backend.services.auth_service import AuthService

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    try:
        claims = decode_token(
            token=credentials.credentials,
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if claims.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    return auth_service.get_user_by_id(claims["sub"])


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    if credentials is None:
        return None

    try:
        claims = decode_token(
            token=credentials.credentials,
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    except TokenError:
        return None

    if claims.get("type") != "access":
        return None

    return auth_service.get_user_by_id(claims["sub"])


async def get_current_user_ws(websocket, token: str):
    """WebSocket 专用的 JWT 认证。

    认证失败时关闭 WebSocket 连接并返回 None。
    认证成功时返回用户对象。

    Args:
        websocket: FastAPI WebSocket 实例
        token: 从 query parameter 提取的 JWT access_token

    Returns:
        用户对象（AuthUser），认证失败返回 None
    """
    from backend.config import get_settings
    from backend.dependencies import get_auth_service

    settings = get_settings()
    auth_service = get_auth_service()

    try:
        claims = decode_token(
            token=token,
            secret_key=settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    except TokenError:
        await websocket.close(code=4001, reason="invalid_token")
        return None

    if claims.get("type") != "access":
        await websocket.close(code=4001, reason="invalid_token_type")
        return None

    return auth_service.get_user_by_id(claims["sub"])
