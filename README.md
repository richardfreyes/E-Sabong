# Event Platform — white-label, multi-tenant event & match management

A commercial SaaS for running events and matches (Meron vs Wala), publishing
official results, managing agent networks, and tracking **non-monetary
demonstration points**. Sold two ways:

- **Shared SaaS** — tenants rent a workspace on a monthly/annual subscription.
- **Dedicated license** — the same codebase installed for one customer, enforced by an Ed25519-signed license.

> **Product boundary.** There is no wagering, betting on outcomes, settlement,
> cash-out, or video broadcasting. Demonstration points have no monetary value,
> cannot be redeemed, and the ledger has no relationship to matches or results.
> Transfers only flow *down* the account hierarchy. See
> [docs/architecture.md](docs/architecture.md#product-invariants).

## What's inside

| Path | Contents |
|---|---|
| `api/` | FastAPI backend (Python 3.12, SQLAlchemy 2 async, Pydantic v2, Celery, Redis) |
| `web/` | Angular 22 workspace: `owner`, `admin`, `player` apps + shared libraries in `web/shared/` |
| `db/` | Alembic migrations, init script for DB roles, backup/restore scripts |
| `infrastructure/` | Caddy (HTTPS, on-demand TLS), web image Dockerfile, Postgres and Redis config |
| `docs/` | Architecture, database, API, deployment, testing, implementation plan, `openapi.json` |

Three portals:

| Portal | URL (production) | Users |
|---|---|---|
| Owner console | `https://owner.<base-domain>` | Platform staff: tenants, billing, licenses, deployments, flags, audit |
| Tenant admin | `https://<tenant>.<base-domain>/admin/` (or custom domain) | Admins, declarators, operators, master agents, agents |
| Player portal | `https://<tenant>.<base-domain>/` (or custom domain) | Players |

## Quick start (local)

Requirements: Docker, Python 3.12, Node 22+.

```bash
cp .env.example .env            # then set the CHANGE_ME values (any strings work locally)
                                # local defaults: ENVIRONMENT=development, BASE_DOMAIN=localhost,
                                # PLATFORM_HOST=owner.localhost, CADDY_TLS_MODE=internal, COOKIE_SECURE=false
docker compose --profile dev up -d --build     # postgres, redis, migrate, api, worker, beat, caddy, mailpit
docker compose run --rm api python -m app.cli seed
docker compose run --rm api python -m app.cli create-platform-admin --email owner@example.com --name "Owner"
docker compose run --rm api python -m app.cli demo-tenant   # tenant "demo" with sample data
docker compose run --rm api python -m app.cli demo-derby     # optional: an 80-fight card for the player portal
```

Open (accept Caddy's local certificate once):

- Owner console: <https://owner.localhost>
- Demo player portal: <https://demo.localhost> — `player1` / `DemoPass#2026`
- Demo admin portal: <https://demo.localhost/admin/> — `admin` / `DemoPass#2026`
- Demo Declarator desk: <https://demo.localhost/admin/> — `declarator` / `DemoPass#2026` (assigned to *Grand Derby Demo*; `admin` approves)
- Mailpit (outgoing mail): <http://localhost:8025>

For hot-reload development of individual pieces (API on the host, Angular dev
servers on 4200–4202), see [docs/testing.md](docs/testing.md#local-development-loop).

## Tests

```bash
docker compose up -d postgres redis
cd api && python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest   # 89 tests
cd web && npm ci && npm run test:unit                                                         # 26 tests
cd web && npx playwright install chromium && npm run e2e                                      # 6 browser tests
```

Coverage and setup details: [docs/testing.md](docs/testing.md).

## Documentation

- [Implementation plan](docs/implementation-plan.md) — milestones, acceptance criteria, status
- [Architecture](docs/architecture.md) — tenancy, isolation, auth, RBAC, realtime, theming, licensing
- [Database](docs/database.md) — schema, relationships, RLS, grants, migration rules
- [API](docs/api.md) — conventions and endpoint catalogue; full contract in [docs/openapi.json](docs/openapi.json), live at `/api/docs`
- [Deployment](docs/deployment.md) — production setup, HTTPS, custom domains, backups, upgrades, dedicated installs
- [Testing](docs/testing.md)

The Declarator workspace is part of the tenant Admin app at `/admin/declarator`; see [setup and verification](docs/declarator/README.md).
