import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.modules.auth.models import AccountStatus
from app.modules.users.models import AccountType
from app.shared.schemas import Schema


class RoleRef(Schema):
    id: uuid.UUID
    name: str


class UserOut(Schema):
    id: uuid.UUID
    username: str
    email: str | None
    full_name: str
    phone: str | None
    account_type: AccountType
    parent_id: uuid.UUID | None
    parent_username: str | None = None
    status: AccountStatus
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: list[RoleRef] = []
    balance: int | None = None


class UserCreate(Schema):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr | None = None
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    account_type: AccountType
    parent_id: uuid.UUID | None = None
    password: str | None = Field(
        default=None,
        min_length=10,
        max_length=128,
        description="Omit to generate a temporary password that must be changed at first sign-in",
    )
    role_ids: list[uuid.UUID] | None = Field(default=None, description="Defaults to the system role for the account type")


class UserCreated(Schema):
    user: UserOut
    temporary_password: str | None = None


class UserUpdate(Schema):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    parent_id: uuid.UUID | None = None
    role_ids: list[uuid.UUID] | None = None


class PasswordReset(Schema):
    temporary_password: str


class RoleOut(Schema):
    id: uuid.UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]
    user_count: int = 0


class RoleCreate(Schema):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_\-]+$")
    description: str | None = Field(default=None, max_length=300)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(Schema):
    description: str | None = Field(default=None, max_length=300)
    permissions: list[str] | None = None


class PermissionOut(Schema):
    code: str
    description: str


class HierarchyNode(Schema):
    id: uuid.UUID
    username: str
    full_name: str
    account_type: AccountType
    status: AccountStatus
    parent_id: uuid.UUID | None
    balance: int
    child_count: int
