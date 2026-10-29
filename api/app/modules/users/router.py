import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, select

from app.core.deps import CurrentUser, Meta, TenantDb, require_any_permission, require_permissions
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.permissions import PERMISSION_DESCRIPTIONS, P
from app.modules.agents.service import child_counts, downline_ids_select, is_in_downline
from app.modules.audit import service as audit
from app.modules.auth.models import AccountStatus
from app.modules.users import service
from app.modules.users.models import AccountType, Role, RolePermission, TenantUser, UserRole
from app.modules.users.schemas import (
    HierarchyNode,
    PasswordReset,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    UserCreate,
    UserCreated,
    UserOut,
    UserUpdate,
)
from app.shared.schemas import Message, Page, PageDep

router = APIRouter(prefix="/admin", tags=["admin: users"])

UserViewer = Annotated[CurrentUser, Depends(require_any_permission(P.USERS_VIEW_ALL, P.USERS_VIEW_DOWNLINE, P.USERS_MANAGE))]
UserManager = Annotated[CurrentUser, Depends(require_any_permission(P.USERS_MANAGE, P.USERS_MANAGE_DOWNLINE))]
FullUserManager = Annotated[CurrentUser, Depends(require_permissions(P.USERS_MANAGE))]
RoleManager = Annotated[CurrentUser, Depends(require_permissions(P.ROLES_MANAGE))]


async def _one(db: TenantDb, current: CurrentUser, user: TenantUser) -> UserOut:
    [out] = await service.to_out(db, current.tenant.id, [user])
    return out


@router.get("/users", response_model=Page[UserOut])
async def list_users(
    current: UserViewer,
    db: TenantDb,
    params: PageDep,
    account_type: AccountType | None = None,
    status_: Annotated[AccountStatus | None, Query(alias="status")] = None,
    parent_id: uuid.UUID | None = None,
) -> Page[UserOut]:
    items, total = await service.list_users(db, current, params, account_type=account_type, status=status_, parent_id=parent_id)
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("/users", response_model=UserCreated, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, current: UserManager, db: TenantDb, meta: Meta) -> UserCreated:
    user, temporary = await service.create_user(
        db, tenant_id=current.tenant.id, data=body, actor=current, audit_actor=current.actor, meta=meta
    )
    return UserCreated(user=await _one(db, current, user), temporary_password=temporary)


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, current: UserViewer, db: TenantDb) -> UserOut:
    await service.ensure_in_scope(db, current, user_id)
    return await _one(db, current, await service.get_user(db, current.tenant.id, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate, current: UserManager, db: TenantDb, meta: Meta) -> UserOut:
    user = await service.update_user(db, current, user_id, body, meta)
    return await _one(db, current, user)


@router.delete("/users/{user_id}", response_model=Message)
async def delete_user(user_id: uuid.UUID, current: FullUserManager, db: TenantDb, meta: Meta) -> Message:
    await service.soft_delete(db, current, user_id, meta)
    return Message(message="Account deleted")


@router.post("/users/{user_id}/suspend", response_model=UserOut)
async def suspend_user(user_id: uuid.UUID, current: UserManager, db: TenantDb, meta: Meta) -> UserOut:
    return await _one(db, current, await service.set_status(db, current, user_id, AccountStatus.SUSPENDED, meta))


@router.post("/users/{user_id}/activate", response_model=UserOut)
async def activate_user(user_id: uuid.UUID, current: UserManager, db: TenantDb, meta: Meta) -> UserOut:
    return await _one(db, current, await service.set_status(db, current, user_id, AccountStatus.ACTIVE, meta))


@router.post("/users/{user_id}/reset-password", response_model=PasswordReset)
async def reset_password(user_id: uuid.UUID, current: UserManager, db: TenantDb, meta: Meta) -> PasswordReset:
    return PasswordReset(temporary_password=await service.reset_password(db, current, user_id, meta))


# ---------------------------------------------------------------- hierarchy


@router.get("/hierarchy", response_model=list[HierarchyNode])
async def hierarchy_children(
    current: UserViewer,
    db: TenantDb,
    parent_id: Annotated[uuid.UUID | None, Query(description="Omit for the top level of your scope")] = None,
) -> list[HierarchyNode]:
    """One level of the account tree; the UI expands nodes lazily."""
    tenant_id = current.tenant.id
    view_all = service.can_view_all(current)
    stmt = select(TenantUser).where(TenantUser.tenant_id == tenant_id, TenantUser.deleted_at.is_(None))
    if parent_id is None:
        if view_all:
            stmt = stmt.where(
                TenantUser.parent_id.is_(None),
                TenantUser.account_type.in_([AccountType.MASTER_AGENT, AccountType.AGENT, AccountType.PLAYER]),
            )
        else:
            stmt = stmt.where(TenantUser.parent_id == current.id)
    else:
        if not view_all and parent_id != current.id and not await is_in_downline(db, tenant_id, current.id, parent_id):
            raise NotFound("Account not found")
        stmt = stmt.where(TenantUser.parent_id == parent_id)
    users = list((await db.execute(stmt.order_by(TenantUser.account_type, TenantUser.username).limit(500))).scalars())
    counts = await child_counts(db, tenant_id, [u.id for u in users])
    balances = await service.balances_for_users(db, tenant_id, [u.id for u in users])
    return [
        HierarchyNode(
            id=u.id,
            username=u.username,
            full_name=u.full_name,
            account_type=u.account_type,
            status=u.status,
            parent_id=u.parent_id,
            balance=balances.get(u.id, 0),
            child_count=counts.get(u.id, 0),
        )
        for u in users
    ]


@router.get("/users/{user_id}/downline", response_model=list[UserOut])
async def downline(user_id: uuid.UUID, current: UserViewer, db: TenantDb) -> list[UserOut]:
    if user_id != current.id:
        await service.ensure_in_scope(db, current, user_id)
    users = list(
        (
            await db.execute(
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == current.tenant.id, TenantUser.id.in_(downline_ids_select(current.tenant.id, user_id))
                )
                .order_by(TenantUser.username)
                .limit(1000)
            )
        ).scalars()
    )
    return await service.to_out(db, current.tenant.id, users)


# ---------------------------------------------------------------- roles and permissions


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(_: RoleManager) -> list[PermissionOut]:
    return [PermissionOut(code=str(p), description=PERMISSION_DESCRIPTIONS[p]) for p in P]


def _role_out(role: Role, counts: dict[uuid.UUID, int]) -> RoleOut:
    return RoleOut(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=sorted(p.permission for p in role.permissions),
        user_count=counts.get(role.id, 0),
    )


async def _get_role(db: TenantDb, tenant_id: uuid.UUID, role_id: uuid.UUID) -> Role:
    role = (await db.execute(select(Role).where(Role.id == role_id, Role.tenant_id == tenant_id))).scalar_one_or_none()
    if role is None:
        raise NotFound("Role not found")
    return role


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    current: Annotated[CurrentUser, Depends(require_any_permission(P.ROLES_MANAGE, P.USERS_MANAGE))], db: TenantDb
) -> list[RoleOut]:
    roles = (
        (await db.execute(select(Role).where(Role.tenant_id == current.tenant.id).order_by(Role.is_system.desc(), Role.name)))
        .scalars()
        .all()
    )
    counts = await service.role_user_counts(db, current.tenant.id)
    return [_role_out(r, counts) for r in roles]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreate, current: RoleManager, db: TenantDb, meta: Meta) -> RoleOut:
    tenant_id = current.tenant.id
    if (await db.execute(select(Role.id).where(Role.tenant_id == tenant_id, Role.name == body.name))).first():
        raise Conflict("A role with this name already exists", code="role_exists")
    perms = service.validate_permissions(body.permissions)
    role = Role(id=uuid.uuid4(), tenant_id=tenant_id, name=body.name, description=body.description, is_system=False)
    db.add(role)
    await db.flush()
    for p in perms:
        db.add(RolePermission(tenant_id=tenant_id, role_id=role.id, permission=p))
    await audit.record(
        db,
        actor=current.actor,
        action="role.created",
        resource_type="role",
        resource_id=role.id,
        tenant_id=tenant_id,
        changes={"name": role.name, "permissions": perms},
        meta=meta,
    )
    await db.flush()
    await db.refresh(role)
    return _role_out(role, {})


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: uuid.UUID, body: RoleUpdate, current: RoleManager, db: TenantDb, meta: Meta) -> RoleOut:
    tenant_id = current.tenant.id
    role = await _get_role(db, tenant_id, role_id)
    changes: dict = {}
    if body.description is not None and body.description != role.description:
        changes["description"] = [role.description, body.description]
        role.description = body.description
    if body.permissions is not None:
        if role.name == "tenant_admin":
            raise Unprocessable("The tenant_admin role always has every permission", code="role_protected")
        perms = service.validate_permissions(body.permissions)
        old = sorted(p.permission for p in role.permissions)
        if perms != old:
            await db.execute(
                delete(RolePermission).where(RolePermission.role_id == role.id, RolePermission.tenant_id == tenant_id)
            )
            for p in perms:
                db.add(RolePermission(tenant_id=tenant_id, role_id=role.id, permission=p))
            changes["permissions"] = [old, perms]
    if changes:
        await audit.record(
            db,
            actor=current.actor,
            action="role.updated",
            resource_type="role",
            resource_id=role.id,
            tenant_id=tenant_id,
            changes=changes,
            meta=meta,
        )
    await db.flush()
    db.expire(role)
    role = await _get_role(db, tenant_id, role_id)
    return _role_out(role, await service.role_user_counts(db, tenant_id))


@router.delete("/roles/{role_id}", response_model=Message)
async def delete_role(role_id: uuid.UUID, current: RoleManager, db: TenantDb, meta: Meta) -> Message:
    tenant_id = current.tenant.id
    role = await _get_role(db, tenant_id, role_id)
    if role.is_system:
        raise Unprocessable("System roles cannot be deleted", code="role_protected")
    in_use = (
        await db.execute(select(UserRole.user_id).where(UserRole.role_id == role.id, UserRole.tenant_id == tenant_id).limit(1))
    ).first()
    if in_use:
        raise Conflict("Remove this role from all accounts first", code="role_in_use")
    await db.delete(role)
    await audit.record(
        db,
        actor=current.actor,
        action="role.deleted",
        resource_type="role",
        resource_id=role_id,
        tenant_id=tenant_id,
        changes={"name": role.name},
        meta=meta,
    )
    return Message(message="Role deleted")
