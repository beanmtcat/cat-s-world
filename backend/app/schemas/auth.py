from __future__ import annotations

from pydantic import EmailStr, Field

from app.schemas.common import ApiModel


class RegisterRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=15, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserPublic(ApiModel):
    id: str
    email: EmailStr
    display_name: str
    role: str
    status: str
    email_verified: bool


class CsrfResponse(ApiModel):
    csrf_token: str
