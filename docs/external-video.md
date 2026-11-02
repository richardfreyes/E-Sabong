# External video contract

No SWC adapter is verified. Captured cURL, website cookies and protected signing are not integration contracts. Obtain official API documentation, authorized credentials, permission for direct browser playback and applicable legal clearance before enabling any provider. No media is proxied or redistributed.

Existing FastAPI OpenAPI at `/api/openapi.json` describes the tenant routes:
- GET/PUT `/api/v1/admin/settings/video-provider` requires settings.manage. Credentials are write only. Omit both credentials to retain them; clear_credentials deletes them.
- POST `/api/v1/admin/settings/video-provider/{test|playback|refresh|reconnect|event}` uses saved configuration.
- POST `/api/v1/video/events/{event_id}/{playback|refresh|reconnect}` requires an authenticated user and published tenant event.
- The same route with `?audience=declarator` checks Declarator permissions and assignment; it also allows `event`. Metadata never updates internal events/results.

Responses carry status, message, optional playback `{type: mp4|hls|dash, url, expires_at?}` and optional projected metadata. `authorized` means the API authorized a source, not that browser playback succeeded. Failures carry a visible state, never a fallback pretending to be live. Test Connection retrieves and validates playback; it does not prove media availability.

## Deployment

Apply Alembic migration 0008. Set VIDEO_CREDENTIALS_KEY to a separate Fernet key generated with `Fernet.generate_key()`, kept in the deployment secret manager. Preserve it across restarts; rotate by decrypting/re-encrypting stored credentials. Set VIDEO_API_HOSTS and VIDEO_MEDIA_HOSTS to JSON arrays of exact approved hostnames, e.g. `["sandbox.authorized-provider.example"]`. Lists default empty. Tenant administrators can change paths, credentials, IDs and URLs within these boundaries without builds. Approve origins only after verifying authorization. API requests require HTTPS/443, public DNS, pinned public connect IP, original TLS verification, no redirects, no environment proxy, bounded body and timeout. Enforce network egress restrictions too. Browser media hosts must also prohibit private DNS and unsafe manifest redirects at the provider/egress layer; nested HLS/DASH resources are fetched directly by the browser and not inspected by this API.

## Adapters

`demo` explicitly serves the repository's licensed Big Buck Bunny clip, labelled Demo video. Enable it in settings for demonstration; it is not a live stream.

`json-bearer-v1` is an explicit integration contract for compatible authorized providers, **not an inferred SWC protocol**:
1. POST configured login path with JSON `{username,password}`; expect `{access_token: string}`.
2. GET playback path with `game_id` and `video_id` query values and Bearer authorization; expect exactly `{type,url,expires_at?}`. The URL must be a provider-issued, browser-safe URL requiring no website credentials or private request headers.
3. GET live-event path the same way; project only `name`, `fight_number`, `status`.

Each manual action makes at most one login and one retrieval, no automatic authentication retries. External requests are throttled per tenant (licensed demo and disabled states need no upstream attempt); playback is cached for up to 20 seconds, bounded by expiry. Reconnect obtains a new documented bearer session. No upstream token is stored, logged or returned. Broadcasting and WebSocket settings are retained for a future officially documented adapter; this adapter does not call them. WebRTC and provider SDK responses are explicitly unsupported until their documented authorization, signaling and cleanup contracts are supplied; they must not be guessed or loaded as arbitrary scripts.

Playback metadata drives native MP4, native HLS or bundled hls.js, and DASH via dash.js. Provider must allow CORS and direct playback. Configure Caddy VIDEO_BROWSER_ORIGINS with space-separated exact HTTPS media/CDN origins (including manifest segment origins) to permit media and fetch requests under CSP. It defaults empty; operator approval of additional origins requires a Caddy restart, while tenant configuration changes within approved origins need no rebuild. Browser errors and authorization expiry require an explicit refresh; no polling or authentication loops.

## Verification evidence

- `cd api && .venv/bin/python -m pytest unit_tests -q`: 50 passed. Provider contract tests use controlled HTTP responses; DNS pinning/Host/SNI, no redirects/proxy, encryption, expiry and normalized failures are covered. This is not a live provider session or Postman network simulation.
- `cd api && .venv/bin/python -m pytest tests/test_video_provider.py tests/test_declarator.py tests/test_isolation.py -q`: 28 passed against real PostgreSQL/Redis, including migration/RLS, credential retention/deletion, permissions, refresh/refetch and official-result preservation.
- Angular Owner/Admin/Player production builds passed; all frontend unit suites passed, with the final Player suite at 42 tests.
- `cd web && npx playwright test e2e/video-provider.spec.ts`: passed in Chromium against real local FastAPI/PostgreSQL/Redis. Saves demo configuration through Admin, checks nonzero video dimensions and advancing playback time in both existing containers, refreshes video, refetches metadata, preserves declaration controls/results, disables provider, and restores prior configuration. Only licensed bundled MP4 media was verified in a browser. External HLS/DASH, provider authentication renewal and real provider playback remain unverified.
- Production dependency audit: zero reported advisories.
- Orbit public-provider search returned no matching public API documentation for the example provider. Official documentation and authorized sandbox access remain required for any provider-specific implementation. No requests were made to the example provider.
