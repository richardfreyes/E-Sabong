# syntax=docker/dockerfile:1.7
# Builds the three Angular portals and serves them with Caddy (build context: repo root).

FROM node:24-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx ng build owner && npx ng build admin && npx ng build player

FROM caddy:2.10-alpine
COPY infrastructure/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist/owner /srv/owner
COPY --from=build /web/dist/admin /srv/admin
COPY --from=build /web/dist/player /srv/player
EXPOSE 80 443
