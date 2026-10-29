# Web portals

Angular 22 workspace with three applications and shared source libraries.

| Project | Dev server | Production path |
|---|---|---|
| `owner` | `npm run start:owner` → http://localhost:4200 | `https://owner.<base-domain>/` |
| `admin` | `npm run start:admin` → http://localhost:4201/admin/ | `https://<tenant-host>/admin/` |
| `player` | `npm run start:player` → http://localhost:4202 | `https://<tenant-host>/` |

Shared code lives in `shared/` (`@shared/models`, `@shared/services`,
`@shared/components`, `@shared/guards`, `@shared/interceptors`,
`@shared/directives`, `@shared/pipes`); styles in `shared/styles/` (Material 3
theme + Tailwind v4 mapped onto the same tokens).

Dev servers proxy `/api` to `http://localhost:8000` (`proxy.conf.json`). Select a
tenant with `?tenant=<slug>` (default `demo`); this only works while the API runs
with `ALLOW_TENANT_HEADER=true`.

```bash
npm run build:all     # production builds → dist/{owner,admin,player}
npm run test:unit     # Vitest unit tests (shared library specs run with the owner target)
npm run e2e           # Playwright, see ../docs/testing.md
```

See [../docs/architecture.md](../docs/architecture.md#frontend) for structure and theming.
