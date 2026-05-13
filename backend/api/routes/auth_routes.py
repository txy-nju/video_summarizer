from fastapi import APIRouter, Depends, HTTPException, status

from backend.auth.dependencies import get_current_user
from backend.auth.models import (
    CurrentUserResponse,
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
)
from backend.auth.utils import TokenError, decode_token
from backend.config import Settings
from backend.dependencies import get_app_settings, get_auth_service
from backend.services.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=CurrentUserResponse)
async def register(payload: RegisterRequest, auth_service: AuthService = Depends(get_auth_service)):
    user = auth_service.register_user(payload.username, payload.password)
    return CurrentUserResponse(data=user)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, auth_service: AuthService = Depends(get_auth_service)):
    token_data = auth_service.authenticate_user(payload.username, payload.password, payload.device_id)
    return TokenResponse(data=token_data)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshTokenRequest,
    settings: Settings = Depends(get_app_settings),
    auth_service: AuthService = Depends(get_auth_service),
):
    try:
        claims = decode_token(
            token=payload.refresh_token,
            secret_key=settings.jwt_refresh_secret_key,
            algorithm=settings.jwt_algorithm,
        )
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if claims.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    if claims.get("device_id") != payload.device_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device mismatch")

    token_data = auth_service.refresh_access_token(
        user_id=claims["sub"],
        username=claims.get("username", ""),
        device_id=payload.device_id,
    )
    return TokenResponse(data=token_data)


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(current_user=Depends(get_current_user)):
    return CurrentUserResponse(data=current_user)
