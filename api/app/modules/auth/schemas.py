import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.modules.auth.models import PlatformRole
from app.modules.users.models import AccountType
from app.shared.schemas import Schema


class LoginRequest(Schema):
    username: str = Field(min_length=1, max_length=254, description="Username or email")
    password: str = Field(min_length=1, max_length=128)


class PlatformLoginRequest(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(Schema):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr | None = None
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    password: str = Field(min_length=10, max_length=128)


class TenantSummary(Schema):
    id: uuid.UUID
    slug: str
    name: str


class TenantProfile(Schema):
    id: uuid.UUID
    username: str
    email: str | None
    full_name: str
    phone: str | None
    account_type: AccountType
    parent_id: uuid.UUID | None
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    permissions: list[str]
    roles: list[str]
    tenant: TenantSummary


class PlatformProfile(Schema):
    id: uuid.UUID
    email: str
    full_name: str
    role: PlatformRole
    last_login_at: datetime | None


class TokenResponse(Schema):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class TenantLoginResponse(TokenResponse):
    user: TenantProfile


class PlatformLoginResponse(TokenResponse):
    user: PlatformProfile


class ProfileUpdate(Schema):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)


class PasswordChange(Schema):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
