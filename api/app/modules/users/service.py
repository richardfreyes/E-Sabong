import uuid
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.permissions import ALL_PERMISSIONS, DEFAULT_ROLE_FOR_TYPE, SYSTEM_ROLES, P
from app.core.security import (
    generate_temporary_password,
    hash_password,
    utcnow,
    validate_password_policy,
)
from app.modules.agents.service import downline_ids_select, is_in_downline
from app.modules.audit import service as audit
from app.modules.audit.service import Actor, RequestMeta
from app.modules.auth.models import AccountStatus, SubjectType
from app.modules.demo_points.models import AccountKind, PointAccount
from app.modules.subscriptions.limits import ensure_user_capacity
from app.modules.users.models import (
    AGENT_TYPES,
    HIERARCHY_PARENTS,
    STAFF_TYPES,
    AccountType,
    Role,
    RolePermission,
    TenantUser,
    UserRole,
)
from app.modules.users.schemas import RoleRef, UserCreate, UserOut, UserUpdate
from app.shared.repository import get_scoped
from app.shared.schemas import PageParams, paginate

AUDITED_FIELDS = ("email", "full_name", "phone", "parent_id", "status")


def _snapshot(user: TenantUser) -> dict[str, Any]:
    return {f: getattr(user, f) for f in AUDITED_FIELDS}


# ---------------------------------------------------------------- roles


async def seed_system_roles(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for name, (description, _perms) in SYSTEM_ROLES.items():
        role = Role(id=uuid.uuid4(), tenant_id=tenant_id, name=name, description=description, is_system=True)
        db.add(role)
        roles[name] = role
    await db.flush()
    for name, (_, perms) in SYSTEM_ROLES.items():
        for p in sorted(perms):
            db.add(RolePermission(tenant_id=tenant_id, role_id=roles[name].id, permission=str(p)))
    await db.flush()
    return roles


async def role_by_name(db: AsyncSession, tenant_id: uuid.UUID, name: str) -> Role:
    role = (await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.name == name))).scalar_one_or_none()
    if role is None:
        raise NotFound(f"Role '{name}' does not exist")
    return role


async def roles_for_users(db: AsyncSession, tenant_id: uuid.UUID, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[RoleRef]]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(UserRole.user_id, Role.id, Role.name)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.tenant_id == tenant_id, UserRole.user_id.in_(user_ids))
        .order_by(Role.name)
    )
    out: dict[uuid.UUID, list[RoleRef]] = {}
    for uid, rid, name in rows.all():
        out.setdefault(uid, []).append(RoleRef(id=rid, name=name))
    return out


async def balances_for_users(db: AsyncSession, tenant_id: uuid.UUID, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(PointAccount.user_id, PointAccount.balance).where(
            PointAccount.tenant_id == tenant_id, PointAccount.user_id.in_(user_ids)
        )
    )
    return {uid: int(bal) for uid, bal in rows.all()}


async def to_out(db: AsyncSession, tenant_id: uuid.UUID, users: list[TenantUser]) -> list[UserOut]:
    ids = [u.id for u in users]
    roles = await roles_for_users(db, tenant_id, ids)
    balances = await balances_for_users(db, tenant_id, ids)
    parent_ids = {u.parent_id for u in users if u.parent_id}
    parents: dict[uuid.UUID, str] = {}
    if parent_ids:
        rows = await db.execute(
            select(TenantUser.id, TenantUser.username).where(TenantUser.tenant_id == tenant_id, TenantUser.id.in_(parent_ids))
        )
        parents = dict(rows.tuples().all())
    out = []
    for u in users:
        item = UserOut.model_validate(u, from_attributes=True)
        item.roles = roles.get(u.id, [])
        item.balance = balances.get(u.id)
        item.parent_username = parents.get(u.parent_id) if u.parent_id else None
        out.append(item)
    return out


async def _is_admin(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    admin_role = await role_by_name(db, tenant_id, "tenant_admin")
    row = await db.execute(
        select(UserRole.user_id).where(
            UserRole.tenant_id == tenant_id,
            UserRole.user_id == user_id,
            UserRole.role_id == admin_role.id,
        )
    )
    return row.first() is not None


async def _count_other_active_admins(db: AsyncSession, tenant_id: uuid.UUID, excluding: uuid.UUID) -> int:
    admin_role = await role_by_name(db, tenant_id, "tenant_admin")
    stmt = (
        select(func.count(func.distinct(TenantUser.id)))
        .join(UserRole, UserRole.user_id == TenantUser.id)
        .where(
            TenantUser.tenant_id == tenant_id,
            UserRole.role_id == admin_role.id,
            TenantUser.status == AccountStatus.ACTIVE,
            TenantUser.deleted_at.is_(None),
            TenantUser.id != excluding,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def _guard_last_admin(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if await _is_admin(db, tenant_id, user_id) and await _count_other_active_admins(db, tenant_id, user_id) == 0:
        raise Conflict("At least one active tenant administrator is required", code="last_admin")


# ---------------------------------------------------------------- scope


def can_view_all(actor: CurrentUser) -> bool:
    return actor.has_any(P.USERS_VIEW_ALL, P.USERS_MANAGE)


async def ensure_in_scope(db: AsyncSession, actor: CurrentUser, user_id: uuid.UUID) -> None:
    if can_view_all(actor):
        return
    if actor.has_any(P.USERS_VIEW_DOWNLINE, P.USERS_MANAGE_DOWNLINE) and await is_in_downline(
        db, actor.tenant.id, actor.id, user_id
    ):
        return
    raise NotFound("User not found")


async def get_user(db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantUser:
    user = await get_scoped(db, TenantUser, user_id, tenant_id)
    if user is None:
        raise NotFound("User not found")
    return user


async def list_users(
    db: AsyncSession,
    actor: CurrentUser,
    params: PageParams,
    *,
    account_type: AccountType | None = None,
    status: AccountStatus | None = None,
    parent_id: uuid.UUID | None = None,
) -> tuple[list[UserOut], int]:
    tenant_id = actor.tenant.id
    stmt = select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
    if not can_view_all(actor):
        stmt = stmt.where(TenantUser.id.in_(downline_ids_select(tenant_id, actor.id)))
    if account_type:
        stmt = stmt.where(TenantUser.account_type == account_type)
    if status:
        stmt = stmt.where(TenantUser.status == status)
    if parent_id:
        stmt = stmt.where(TenantUser.parent_id == parent_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(TenantUser.username.ilike(like), TenantUser.full_name.ilike(like), TenantUser.email.ilike(like)))
    stmt = stmt.order_by(
        params.order_by(
            {
                "username": TenantUser.username,
                "full_name": TenantUser.full_name,
                "created_at": TenantUser.created_at,
                "last_login_at": TenantUser.last_login_at,
            },
            TenantUser.created_at.desc(),
        )
    )
    users, total = await paginate(db, stmt, params)
    return await to_out(db, tenant_id, users), total


# ---------------------------------------------------------------- create / update


async def _validate_parent(
    db: AsyncSession, tenant_id: uuid.UUID, account_type: AccountType, parent_id: uuid.UUID | None
) -> TenantUser | None:
    allowed = HIERARCHY_PARENTS[account_type]
    if parent_id is None:
        if None not in allowed:
            raise Unprocessable(f"A {account_type.value} must have a parent account", code="invalid_parent")
        return None
    parent = await get_scoped(db, TenantUser, parent_id, tenant_id)
    if parent is None:
        raise Unprocessable("Parent account not found", code="invalid_parent")
    if parent.account_type not in allowed:
        raise Unprocessable(
            f"A {account_type.value} cannot be placed under a {parent.account_type.value}",
            code="invalid_parent",
        )
    if parent.status != AccountStatus.ACTIVE:
        raise Unprocessable("Parent account is suspended", code="invalid_parent")
    return parent


async def _resolve_roles(
    db: AsyncSession, tenant_id: uuid.UUID, account_type: AccountType, role_ids: list[uuid.UUID] | None
) -> list[Role]:
    if not role_ids:
        return [await role_by_name(db, tenant_id, DEFAULT_ROLE_FOR_TYPE[account_type.value])]
    roles = list((await db.execute(select(Role).where(Role.tenant_id == tenant_id, Role.id.in_(role_ids)))).scalars().all())
    if len(roles) != len(set(role_ids)):
        raise Unprocessable("One or more roles do not exist", code="invalid_role")
    # The declaration workflow only accepts Declarator accounts, so the role on any other account
    # type grants nothing usable and leaves the account looking broken instead of misconfigured.
    declarator_role = DEFAULT_ROLE_FOR_TYPE[AccountType.DECLARATOR.value]
    if account_type != AccountType.DECLARATOR and any(r.is_system and r.name == declarator_role for r in roles):
        raise Unprocessable(
            "The Declarator role can only be assigned to Declarator accounts. "
            "Create an account with the Declarator type instead.",
            code="invalid_role",
        )
    return roles


async def _ensure_unique(
    db: AsyncSession, tenant_id: uuid.UUID, *, username: str | None, email: str | None, exclude: uuid.UUID | None = None
) -> None:
    base = select(TenantUser.id).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
    if exclude is not None:
        base = base.where(TenantUser.id != exclude)
    if username and (await db.execute(base.where(func.lower(TenantUser.username) == username.lower()))).first():
        raise Conflict("Username is already taken", code="username_taken")
    if email and (await db.execute(base.where(func.lower(TenantUser.email) == email.lower()))).first():
        raise Conflict("Email is already in use", code="email_taken")


async def create_user(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    data: UserCreate,
    actor: CurrentUser | None,
    audit_actor: Actor,
    meta: RequestMeta,
    enforce_limits: bool = True,
) -> tuple[TenantUser, str | None]:
    parent_id = data.parent_id
    if actor is not None and not actor.has(P.USERS_MANAGE):
        # Downline managers (agents) can only create agents/players beneath themselves.
        if not actor.has(P.USERS_MANAGE_DOWNLINE) or actor.user.account_type not in AGENT_TYPES:
            raise Forbidden("You cannot create accounts", code="permission_denied")
        if data.account_type in STAFF_TYPES or data.account_type == AccountType.MASTER_AGENT:
            raise Forbidden(f"You cannot create {data.account_type.value} accounts", code="permission_denied")
        if data.role_ids:
            raise Forbidden("You cannot assign roles", code="permission_denied")
        parent_id = parent_id or actor.id
        if parent_id != actor.id and not await is_in_downline(db, tenant_id, actor.id, parent_id):
            raise Forbidden("Parent must be you or within your downline", code="permission_denied")

    await _validate_parent(db, tenant_id, data.account_type, parent_id)
    if enforce_limits:
        await ensure_user_capacity(db, tenant_id)
    await _ensure_unique(db, tenant_id, username=data.username, email=data.email)

    temporary = None
    if data.password:
        validate_password_policy(data.password)
        password = data.password
    else:
        temporary = password = generate_temporary_password()

    roles = await _resolve_roles(db, tenant_id, data.account_type, data.role_ids)
    user = TenantUser(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        username=data.username,
        email=data.email,
        full_name=data.full_name,
        phone=data.phone,
        account_type=data.account_type,
        parent_id=parent_id,
        password_hash=hash_password(password),
        must_change_password=temporary is not None,
        created_by_id=actor.id if actor else None,
    )
    db.add(user)
    await db.flush()
    for role in roles:
        db.add(UserRole(tenant_id=tenant_id, user_id=user.id, role_id=role.id))
    db.add(PointAccount(tenant_id=tenant_id, kind=AccountKind.USER, user_id=user.id, balance=0))
    await audit.record(
        db,
        actor=audit_actor,
        action="user.created",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=tenant_id,
        changes={
            "username": user.username,
            "account_type": user.account_type,
            "parent_id": parent_id,
            "roles": [r.name for r in roles],
        },
        meta=meta,
    )
    await db.flush()
    return user, temporary


async def update_user(
    db: AsyncSession, actor: CurrentUser, user_id: uuid.UUID, data: UserUpdate, meta: RequestMeta
) -> TenantUser:
    tenant_id = actor.tenant.id
    if not actor.has_any(P.USERS_MANAGE, P.USERS_MANAGE_DOWNLINE):
        raise Forbidden("You cannot edit accounts", code="permission_denied")
    await ensure_in_scope(db, actor, user_id)
    user = await get_user(db, tenant_id, user_id)
    before = _snapshot(user)
    fields = data.model_dump(exclude_unset=True)

    if "parent_id" in fields and fields["parent_id"] != user.parent_id:
        new_parent = fields["parent_id"]
        if new_parent == user.id:
            raise Unprocessable("An account cannot be its own parent", code="invalid_parent")
        if not actor.has(P.USERS_MANAGE) and (
            new_parent is None or (new_parent != actor.id and not await is_in_downline(db, tenant_id, actor.id, new_parent))
        ):
            raise Forbidden("Parent must be you or within your downline", code="permission_denied")
        await _validate_parent(db, tenant_id, user.account_type, new_parent)
        if new_parent is not None and await is_in_downline(db, tenant_id, user.id, new_parent):
            raise Unprocessable("Cannot move an account beneath its own downline", code="invalid_parent")
        user.parent_id = new_parent

    if fields.get("email") and fields["email"] != user.email:
        await _ensure_unique(db, tenant_id, username=None, email=fields["email"], exclude=user.id)
    for key in ("email", "full_name", "phone"):
        if key in fields:
            setattr(user, key, fields[key])

    if data.role_ids is not None:
        if not actor.has(P.USERS_MANAGE):
            raise Forbidden("You cannot assign roles", code="permission_denied")
        roles = await _resolve_roles(db, tenant_id, user.account_type, data.role_ids)
        if "tenant_admin" not in {r.name for r in roles}:
            await _guard_last_admin(db, tenant_id, user.id)
        await db.execute(delete(UserRole).where(UserRole.tenant_id == tenant_id, UserRole.user_id == user.id))
        for role in roles:
            db.add(UserRole(tenant_id=tenant_id, user_id=user.id, role_id=role.id))

    await db.flush()
    changes = audit.diff(before, _snapshot(user))
    if data.role_ids is not None:
        changes["role_ids"] = [None, [str(r) for r in data.role_ids]]
    if changes:
        await audit.record(
            db,
            actor=actor.actor,
            action="user.updated",
            resource_type="tenant_user",
            resource_id=user.id,
            tenant_id=tenant_id,
            changes=changes,
            meta=meta,
        )
    return user


async def set_status(
    db: AsyncSession, actor: CurrentUser, user_id: uuid.UUID, status: AccountStatus, meta: RequestMeta
) -> TenantUser:
    from app.modules.auth.service import revoke_subject_sessions

    tenant_id = actor.tenant.id
    if not actor.has_any(P.USERS_MANAGE, P.USERS_MANAGE_DOWNLINE):
        raise Forbidden("You cannot change account status", code="permission_denied")
    await ensure_in_scope(db, actor, user_id)
    if user_id == actor.id:
        raise Unprocessable("You cannot change your own status", code="self_action")
    user = await get_user(db, tenant_id, user_id)
    if user.status == status:
        return user
    if status == AccountStatus.SUSPENDED:
        await _guard_last_admin(db, tenant_id, user.id)
        await revoke_subject_sessions(db, SubjectType.TENANT, user.id)
    old = user.status
    user.status = status
    action = "user.suspended" if status == AccountStatus.SUSPENDED else "user.activated"
    await audit.record(
        db,
        actor=actor.actor,
        action=action,
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=tenant_id,
        changes={"status": [old, status]},
        meta=meta,
    )
    await db.flush()
    return user


async def soft_delete(db: AsyncSession, actor: CurrentUser, user_id: uuid.UUID, meta: RequestMeta) -> None:
    from app.modules.auth.service import revoke_subject_sessions

    tenant_id = actor.tenant.id
    if user_id == actor.id:
        raise Unprocessable("You cannot delete your own account", code="self_action")
    user = await get_user(db, tenant_id, user_id)
    has_children = (
        await db.execute(
            select(TenantUser.id)
            .where(TenantUser.tenant_id == tenant_id, TenantUser.parent_id == user.id, TenantUser.deleted_at.is_(None))
            .limit(1)
        )
    ).first()
    if has_children:
        raise Conflict("Reassign or remove this account's downline first", code="has_downline")
    await _guard_last_admin(db, tenant_id, user.id)
    await revoke_subject_sessions(db, SubjectType.TENANT, user.id)
    user.deleted_at = utcnow()
    user.status = AccountStatus.SUSPENDED
    await audit.record(
        db,
        actor=actor.actor,
        action="user.deleted",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=tenant_id,
        changes={"username": user.username},
        meta=meta,
    )


async def reset_password(db: AsyncSession, actor: CurrentUser, user_id: uuid.UUID, meta: RequestMeta) -> str:
    from app.modules.auth.service import revoke_subject_sessions

    tenant_id = actor.tenant.id
    if not actor.has_any(P.USERS_MANAGE, P.USERS_MANAGE_DOWNLINE):
        raise Forbidden("You cannot reset passwords", code="permission_denied")
    await ensure_in_scope(db, actor, user_id)
    if user_id == actor.id:
        raise Unprocessable("Use the change password form for your own account", code="self_action")
    user = await get_user(db, tenant_id, user_id)
    temporary = generate_temporary_password()
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    await revoke_subject_sessions(db, SubjectType.TENANT, user.id)
    await audit.record(
        db,
        actor=actor.actor,
        action="user.password_reset",
        resource_type="tenant_user",
        resource_id=user.id,
        tenant_id=tenant_id,
        meta=meta,
    )
    return temporary


# ---------------------------------------------------------------- role management


def validate_permissions(perms: list[str]) -> list[str]:
    valid = {str(p) for p in ALL_PERMISSIONS}
    unknown = sorted(set(perms) - valid)
    if unknown:
        raise Unprocessable("Unknown permissions", code="invalid_permission", details={"unknown": unknown})
    return sorted(set(perms))


async def role_user_counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(UserRole.role_id, func.count())
        .join(TenantUser, TenantUser.id == UserRole.user_id)
        .where(UserRole.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
        .group_by(UserRole.role_id)
    )
    return {rid: int(n) for rid, n in rows.all()}
