# syntax=docker/dockerfile:1.7
# Builds the three Angular portals and serves them with Caddy (build context: repo root).
#
# OWNER_BASE_HREF: "/" serves the owner console at the root of PLATFORM_HOST (single-host
# Caddyfile); the Contabo layout (docker-compose.contabo.yml) builds it with "/owner/".

FROM node:24-alpine AS build
ARG OWNER_BASE_HREF=/
ARG WEB_CONFIGURATION=production
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN case "$WEB_CONFIGURATION" in production|uat) ;; *) echo "Expected production or uat build" >&2; exit 1 ;; esac \
    && npx ng build owner --configuration "$WEB_CONFIGURATION" --base-href "$OWNER_BASE_HREF" \
    && npx ng build admin --configuration "$WEB_CONFIGURATION" \
    && npx ng build player --configuration "$WEB_CONFIGURATION"
# UAT and production images must never ship source maps, a development build, secret values or
# local paths (local `ng serve` keeps all of that for debugging).
RUN node scripts/verify-prod-bundle.mjs dist/owner dist/admin dist/player

FROM caddy:2.10-alpine
COPY infrastructure/caddy/Caddyfile infrastructure/caddy/Caddyfile.origin infrastructure/caddy/snippets.caddy /etc/caddy/
COPY --from=build /web/dist/owner /srv/owner
COPY --from=build /web/dist/admin /srv/admin
COPY --from=build /web/dist/player /srv/player
EXPOSE 80 443
