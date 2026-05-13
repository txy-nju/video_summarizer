from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20)
    device_id: str = Field(min_length=1, max_length=128)


class UserView(BaseModel):
    user_id: str
    username: str


class TokenResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserView


class TokenResponse(BaseModel):
    status: str = "success"
    data: TokenResponseData


class CurrentUserResponse(BaseModel):
    status: str = "success"
    data: UserView
