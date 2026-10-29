"""Operational commands.

python -m app.cli seed                      # default plans and feature flags (idempotent)
python -m app.cli create-platform-admin --email ops@example.com --name "Ops" [--role super_admin]
python -m app.cli generate-license-keys     # Ed25519 key pair for license signing
python -m app.cli demo-tenant               # development data set (refused in production)
python -m app.cli demo-derby                # a full derby card for an existing demo tenant
"""

import argparse
import asyncio
import getpass
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import dispose_engines, platform_session
from app.core.permissions import P
from app.core.redis import close_redis
from app.core.security import hash_password, utcnow, validate_password_policy
from app.modules.audit.service import Actor
from app.modules.auth.models import PlatformRole, PlatformUser
from app.modules.subscriptions.models import BillingCycle, PlanKind, SubscriptionPlan
from app.modules.tenants.models import ALL_MODULES, FeatureFlag, Tenant

DEFAULT_PLANS = [
    {
        "code": "starter",
        "name": "Starter",
        "kind": PlanKind.SAAS,
        "sort_order": 10,
        "description": "For a single venue getting started",
        "price_monthly_cents": 4900,
        "price_annual_cents": 49000,
        "trial_days": 14,
        "limits": {"max_users": 250, "max_events_per_month": 8},
        "modules": ["events", "matches", "notifications"],
    },
    {
        "code": "professional",
        "name": "Professional",
        "kind": PlanKind.SAAS,
        "sort_order": 20,
        "description": "Agent networks, demonstration points and reporting",
        "price_monthly_cents": 14900,
        "price_annual_cents": 149000,
        "trial_days": 14,
        "limits": {"max_users": 2500, "max_events_per_month": 40},
        "modules": list(ALL_MODULES),
    },
    {
        "code": "enterprise",
        "name": "Enterprise",
        "kind": PlanKind.SAAS,
        "sort_order": 30,
        "description": "Unlimited accounts and events",
        "price_monthly_cents": 49900,
        "price_annual_cents": 499000,
        "trial_days": 30,
        "limits": {},
        "modules": list(ALL_MODULES),
    },
    {
        "code": "maintenance-standard",
        "name": "Standard Maintenance",
        "kind": PlanKind.MAINTENANCE,
        "sort_order": 100,
        "description": "Updates and support for dedicated licensed installations",
        "price_monthly_cents": 29900,
        "price_annual_cents": 299000,
        "trial_days": 0,
        "limits": {},
        "modules": [],
    },
]

DEFAULT_FLAGS = [
    ("live_updates", "Push match, result and points updates to browsers over WebSocket", True),
    ("csv_exports", "Allow CSV export of tenant reports", True),
]


async def seed() -> None:
    async with platform_session() as db:
        for spec in DEFAULT_PLANS:
            exists = (
                await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == spec["code"]))
            ).scalar_one_or_none()
            if exists is None:
                db.add(SubscriptionPlan(currency=get_settings().default_currency, **spec))
                print(f"plan created: {spec['code']}")
        for key, description, default in DEFAULT_FLAGS:
            if (await db.execute(select(FeatureFlag.id).where(FeatureFlag.key == key))).first() is None:
                db.add(FeatureFlag(key=key, description=description, default_enabled=default))
                print(f"flag created: {key}")


async def create_platform_admin(email: str, name: str, role: str, password: str | None) -> None:
    password = password or getpass.getpass("Password: ")
    validate_password_policy(password)
    async with platform_session() as db:
        if (await db.execute(select(PlatformUser.id).where(func.lower(PlatformUser.email) == email.lower()))).first():
            print("A platform user with this email already exists", file=sys.stderr)
            sys.exit(1)
        db.add(PlatformUser(email=email, full_name=name, role=PlatformRole(role), password_hash=hash_password(password)))
    print(f"platform user created: {email} ({role})")


def generate_license_keys() -> None:
    from app.modules.licenses.signing import generate_keypair

    private, public = generate_keypair()
    print("# Keep the private key only on the SaaS control plane.")
    print("LICENSE_PRIVATE_KEY_PEM=" + private.strip().replace("\n", "\\n"))
    print("LICENSE_PUBLIC_KEY_PEM=" + public.strip().replace("\n", "\\n"))


async def demo_tenant(slug: str, password: str) -> None:
    """Development data: one tenant with every account type, an event and points."""
    from app.core.database import tenant_session
    from app.core.deps import CurrentUser
    from app.core.tenancy import TenantContext
    from app.modules.audit.service import RequestMeta
    from app.modules.declarator import service as declarator_desk
    from app.modules.demo_points import service as points
    from app.modules.events import service as events
    from app.modules.events.models import EventStatus
    from app.modules.events.schemas import EventCreate, MatchCreate
    from app.modules.matches import service as matches
    from app.modules.results import service as results
    from app.modules.results.models import MatchOutcome
    from app.modules.tenants.service import provision_tenant
    from app.modules.users import service as users
    from app.modules.users.models import AccountType
    from app.modules.users.schemas import UserCreate

    if get_settings().is_production:
        print("demo-tenant is not available in production", file=sys.stderr)
        sys.exit(1)
    validate_password_policy(password)
    await seed()
    async with platform_session() as db:
        if (await db.execute(select(Tenant.id).where(Tenant.slug == slug, Tenant.deleted_at.is_(None)))).first():
            print(f"tenant '{slug}' already exists")
            return
        plan = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == "professional"))).scalar_one()
        result = await provision_tenant(
            db,
            name="Demo Arena",
            slug=slug,
            plan=plan,
            billing_cycle=BillingCycle.MONTHLY,
            admin_email=f"admin@{slug}.example.com",
            admin_name="Demo Administrator",
            with_trial=True,
            actor=Actor.system(),
        )
        result.admin.password_hash = hash_password(password)
        result.admin.must_change_password = False
        tenant = result.tenant
        admin_id = result.admin.id

    ctx = TenantContext(id=tenant.id, slug=tenant.slug, name=tenant.name, status=tenant.status)
    meta = RequestMeta()
    async with tenant_session(tenant.id) as db:
        admin_user = await users.get_user(db, tenant.id, admin_id)
        admin = CurrentUser(user=admin_user, permissions=frozenset(str(p) for p in P), tenant=ctx, session_family=uuid.uuid4())

        async def make(username: str, kind: AccountType, parent: uuid.UUID | None, full_name: str) -> uuid.UUID:
            u, _ = await users.create_user(
                db,
                tenant_id=tenant.id,
                actor=admin,
                audit_actor=admin.actor,
                meta=meta,
                data=UserCreate(username=username, full_name=full_name, account_type=kind, parent_id=parent, password=password),
            )
            return u.id

        await make("operator", AccountType.OPERATOR, None, "Event Operator")
        declarator = await make("declarator", AccountType.DECLARATOR, None, "Results Declarator")
        master = await make("master1", AccountType.MASTER_AGENT, None, "Master Agent One")
        agent = await make("agent1", AccountType.AGENT, master, "Agent One")
        players = [await make(f"player{i}", AccountType.PLAYER, agent, f"Player {i}") for i in range(1, 4)]

        await points.allocate(
            db, admin, user_id=master, amount=100_000, memo="Opening allocation", idempotency_key=f"demo-{slug}-1", meta=meta
        )
        master_user = await users.get_user(db, tenant.id, master)
        master_ctx = CurrentUser(
            user=master_user, permissions=frozenset({str(P.POINTS_TRANSFER)}), tenant=ctx, session_family=uuid.uuid4()
        )
        await points.transfer(
            db, master_ctx, to_user_id=agent, amount=20_000, memo="Agent float", idempotency_key=f"demo-{slug}-2", meta=meta
        )
        agent_user = await users.get_user(db, tenant.id, agent)
        agent_ctx = CurrentUser(
            user=agent_user, permissions=frozenset({str(P.POINTS_TRANSFER)}), tenant=ctx, session_family=uuid.uuid4()
        )
        for i, pid in enumerate(players, start=1):
            await points.transfer(
                db,
                agent_ctx,
                to_user_id=pid,
                amount=1_000 * i,
                memo="Welcome points",
                idempotency_key=f"demo-{slug}-p{i}",
                meta=meta,
            )

        event = await events.create_event(
            db, admin, EventCreate(name="Grand Derby Demo", venue="Demo Arena", starts_at=utcnow() - timedelta(hours=1)), meta
        )
        pairs = [
            ("Red Thunder", "Blue Storm"),
            ("Golden Hawk", "Silver Wing"),
            ("Iron Spur", "Midnight"),
            ("Jade King", "Ash Runner"),
        ]
        created = [
            await matches.create_match(
                db, admin, event.id, MatchCreate(meron_name=m, wala_name=w, meron_details="Entry A", wala_details="Entry B"), meta
            )
            for m, w in pairs
        ]
        await events.transition(db, admin, event.id, EventStatus.SCHEDULED, meta)
        await events.transition(db, admin, event.id, EventStatus.LIVE, meta)
        for match, outcome in zip(created[:2], (MatchOutcome.MERON, MatchOutcome.WALA), strict=True):
            await matches.start_match(db, admin, match.id, meta)
            await results.declare(db, admin, match.id, outcome, "Official result", meta)
        await matches.start_match(db, admin, created[2].id, meta)
        # Fight 3 is in progress, so the Declarator can propose its result and admin can approve it.
        await declarator_desk.assign(db, admin, event.id, declarator, False, meta)

    s = get_settings()
    print(f"demo tenant ready: {s.public_scheme}://{slug}.{s.base_domain}")
    print(f"accounts (password '{password}'): admin, operator, declarator, master1, agent1, player1..3")


DERBY_PREFIXES = (
    "Red Blue Golden Silver Iron Midnight Jade Ash Storm Granite Copper Onyx Crimson Cobalt Amber Ivory Slate Ember Thunder Royal"
).split()
DERBY_SUFFIXES = (
    "Comet Hawk Spur Runner Lancer Baron Arrow Talon Crest Blaze "
    "Striker Warden Falcon Ridge Sabre Monarch Drifter Pilot Rebel Knight"
).split()
DERBY_STABLES = "Ilocos Cebu Davao Bacolod Pampanga Iloilo Cavite Bulacan Negros Batangas".split()


async def demo_derby(slug: str, name: str, fights: int, seed_value: int) -> None:
    """Development data: one busy derby card so the player portal has a full result board."""
    from datetime import timedelta
    from random import Random

    from app.core.database import tenant_session
    from app.core.deps import CurrentUser
    from app.core.tenancy import TenantContext
    from app.modules.audit.service import RequestMeta
    from app.modules.events import service as events
    from app.modules.events.models import EventStatus
    from app.modules.events.schemas import EventCreate, MatchCreate
    from app.modules.matches import service as matches
    from app.modules.results import service as results
    from app.modules.results.models import MatchOutcome
    from app.modules.users.models import AccountType, TenantUser

    if get_settings().is_production:
        print("demo-derby is not available in production", file=sys.stderr)
        sys.exit(1)
    if not 4 <= fights <= 200:
        print("--fights must be between 4 and 200", file=sys.stderr)
        sys.exit(1)

    async with platform_session() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug, Tenant.deleted_at.is_(None)))).scalar_one_or_none()
        if tenant is None:
            print(f"tenant '{slug}' not found; run demo-tenant first", file=sys.stderr)
            sys.exit(1)
        ctx = TenantContext(id=tenant.id, slug=tenant.slug, name=tenant.name, status=tenant.status)

    rng = Random(seed_value)  # noqa: S311 - demonstration data, not security
    meta = RequestMeta()
    # The last fights stay open so the portal has a live fight and an upcoming card.
    in_progress_number = fights - 5
    async with tenant_session(tenant.id) as db:
        admin_user = (
            await db.execute(
                select(TenantUser)
                .where(
                    TenantUser.tenant_id == tenant.id,
                    TenantUser.account_type == AccountType.ADMIN,
                    TenantUser.deleted_at.is_(None),
                )
                .order_by(TenantUser.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if admin_user is None:
            print(f"tenant '{slug}' has no administrator account", file=sys.stderr)
            sys.exit(1)
        admin = CurrentUser(user=admin_user, permissions=frozenset(str(p) for p in P), tenant=ctx, session_family=uuid.uuid4())

        # Fights run roughly every eight minutes, so the card reads as an afternoon of derby.
        start = utcnow() - timedelta(minutes=8 * in_progress_number)
        event = await events.create_event(
            db,
            admin,
            EventCreate(
                name=name,
                venue="Demo Arena, Pit B",
                description=f"A {fights}-fight demonstration card used to exercise the player portal.",
                starts_at=start,
            ),
            meta,
        )
        names = [f"{p} {s}" for p in DERBY_PREFIXES for s in DERBY_SUFFIXES]
        rng.shuffle(names)

        def entry() -> str:
            weight = rng.randrange(2000, 2600) / 1000
            return f"{weight:.3f} kg, {rng.choice(DERBY_STABLES)} stable"

        created = []
        for number in range(1, fights + 1):
            created.append(
                await matches.create_match(
                    db,
                    admin,
                    event.id,
                    MatchCreate(
                        meron_name=names[(number - 1) * 2],
                        wala_name=names[(number - 1) * 2 + 1],
                        meron_details=entry(),
                        wala_details=entry(),
                        scheduled_at=start + timedelta(minutes=8 * (number - 1)),
                    ),
                    meta,
                )
            )
        await events.transition(db, admin, event.id, EventStatus.SCHEDULED, meta)
        await events.transition(db, admin, event.id, EventStatus.LIVE, meta)

        # A realistic spread: mostly decided, with a handful of draws and cancellations.
        outcomes = [MatchOutcome.MERON, MatchOutcome.WALA, MatchOutcome.DRAW, MatchOutcome.NO_CONTEST]
        weights = (46, 44, 7, 3)
        cancelled = set(rng.sample(range(2, in_progress_number), k=max(1, (fights - 6) // 25)))
        corrected = in_progress_number // 3
        decided = 0
        for match in created[: in_progress_number - 1]:
            number = match.match_number
            if number in cancelled:
                await matches.cancel_match(db, admin, match.id, "Entry withdrawn by the handler", meta)
                match.ended_at = start + timedelta(minutes=8 * (number - 1))
                continue
            outcome = rng.choices(outcomes, weights=weights, k=1)[0]
            await matches.start_match(db, admin, match.id, meta)
            _, result = await results.declare(db, admin, match.id, outcome, None, meta)
            decided += 1
            # Backdate the clock so the timeline and result board read like a real card.
            match.started_at = start + timedelta(minutes=8 * (number - 1))
            match.ended_at = match.started_at + timedelta(minutes=rng.randrange(3, 7))
            result.declared_at = match.ended_at
            if number == corrected:
                _, revised = await results.correct(
                    db,
                    admin,
                    match.id,
                    MatchOutcome.WALA if outcome != MatchOutcome.WALA else MatchOutcome.MERON,
                    "Video review after a protest",
                    "Corrected after review",
                    meta,
                )
                revised.declared_at = match.ended_at + timedelta(minutes=4)
        live = created[in_progress_number - 1]
        await matches.start_match(db, admin, live.id, meta)
        live.started_at = start + timedelta(minutes=8 * (in_progress_number - 1))

    print(f"derby '{name}' ready: {fights} fights, {decided} decided, fight {in_progress_number} live")


async def create_tenant(slug: str, name: str, admin_email: str, admin_name: str, plan_code: str | None) -> None:
    """Provision a tenant from the shell (dedicated installs use no plan)."""
    from app.modules.tenants.models import DeploymentMode
    from app.modules.tenants.service import provision_tenant

    s = get_settings()
    async with platform_session() as db:
        plan = None
        if plan_code:
            plan = (await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.code == plan_code))).scalar_one_or_none()
            if plan is None:
                print(f"Unknown plan '{plan_code}' (run seed first)", file=sys.stderr)
                sys.exit(1)
        mode = DeploymentMode.DEDICATED if s.deployment_mode == "dedicated" or plan is None else DeploymentMode.SHARED
        result = await provision_tenant(
            db,
            name=name,
            slug=slug,
            plan=plan,
            billing_cycle=BillingCycle.MONTHLY,
            admin_email=admin_email,
            admin_name=admin_name,
            with_trial=True,
            deployment_mode=mode,
            actor=Actor.system(),
        )
    print(f"tenant '{slug}' created ({mode.value}); admin sign-in: {result.login_url}")
    print(f"username: {result.admin.username}  temporary password: {result.temporary_password}")


async def activate_license(url: str) -> None:
    """Dedicated installs: exchange LICENSE_KEY for a signed token bound to this instance."""
    import socket

    from app.core.redis import get_redis
    from app.modules.licenses import signing
    from app.workers.tasks.licensing import APP_VERSION, _post, instance_fingerprint

    s = get_settings()
    if s.deployment_mode != "dedicated" or not s.license_key:
        print("Set DEPLOYMENT_MODE=dedicated and LICENSE_KEY first", file=sys.stderr)
        sys.exit(1)
    status, body = _post(
        url,
        {
            "license_key": s.license_key.get_secret_value(),
            "instance_fingerprint": instance_fingerprint(),
            "hostname": socket.getfqdn(),
            "app_version": APP_VERSION,
        },
    )
    if status != 200 or not body.get("license_token"):
        print(f"Activation failed ({status}): {body.get('error', body)}", file=sys.stderr)
        sys.exit(1)
    token = body["license_token"]
    verified = signing.verify(token)  # fails loudly if LICENSE_PUBLIC_KEY_PEM does not match
    await get_redis().set("license:token", token)
    print(f"activated for {verified.licensee} (tenant {verified.tenant_slug}), expires {verified.expires_at or 'never'}")
    print("Persist this in .env so the install survives a Redis reset:")
    print(f"LICENSE_TOKEN={token}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    p_admin = sub.add_parser("create-platform-admin")
    p_admin.add_argument("--email", required=True)
    p_admin.add_argument("--name", required=True)
    p_admin.add_argument("--role", default="super_admin", choices=[r.value for r in PlatformRole])
    p_admin.add_argument("--password", help="Omit to be prompted")
    sub.add_parser("generate-license-keys")
    p_demo = sub.add_parser("demo-tenant")
    p_demo.add_argument("--slug", default="demo")
    p_demo.add_argument("--password", default="DemoPass#2026")
    p_derby = sub.add_parser("demo-derby")
    p_derby.add_argument("--slug", default="demo")
    p_derby.add_argument("--name", default="Champions Derby")
    p_derby.add_argument("--fights", type=int, default=80)
    p_derby.add_argument("--seed", type=int, default=7, help="Fixed seed keeps the generated card reproducible")
    p_tenant = sub.add_parser("create-tenant")
    p_tenant.add_argument("--slug", required=True)
    p_tenant.add_argument("--name", required=True)
    p_tenant.add_argument("--admin-email", required=True)
    p_tenant.add_argument("--admin-name", default="Administrator")
    p_tenant.add_argument("--plan", default=None, help="Plan code for shared tenants; omit for licensed installs")
    p_act = sub.add_parser("activate-license")
    p_act.add_argument("--url", required=True, help="Control plane /api/v1/licensing/activate URL")
    args = parser.parse_args()

    async def run(coro) -> None:  # type: ignore[no-untyped-def]
        try:
            await coro
        finally:
            await dispose_engines()
            await close_redis()

    if args.command == "seed":
        asyncio.run(run(seed()))
    elif args.command == "create-platform-admin":
        asyncio.run(run(create_platform_admin(args.email, args.name, args.role, args.password)))
    elif args.command == "generate-license-keys":
        generate_license_keys()
    elif args.command == "demo-tenant":
        asyncio.run(run(demo_tenant(args.slug, args.password)))
    elif args.command == "demo-derby":
        asyncio.run(run(demo_derby(args.slug, args.name, args.fights, args.seed)))
    elif args.command == "create-tenant":
        asyncio.run(run(create_tenant(args.slug, args.name, args.admin_email, args.admin_name, args.plan)))
    elif args.command == "activate-license":
        asyncio.run(run(activate_license(args.url)))


if __name__ == "__main__":
    main()
