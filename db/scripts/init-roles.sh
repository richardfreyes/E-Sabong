#!/usr/bin/env bash
# Runs once, as the PostgreSQL superuser, when the data directory is first created
# (mounted into /docker-entrypoint-initdb.d). Creates the three database identities:
#
#   esabong_owner     owns the schema; used only by Alembic migrations
#   esabong_app       API tenant traffic; subject to row-level security
#   esabong_platform  platform-owner routes and workers; BYPASSRLS
#
# Table-level privileges are granted by the migrations, not here.
set -euo pipefail

: "${DB_NAME:=esabong}"
: "${DB_OWNER_PASSWORD:?DB_OWNER_PASSWORD is required}"
: "${DB_APP_PASSWORD:?DB_APP_PASSWORD is required}"
: "${DB_PLATFORM_PASSWORD:?DB_PLATFORM_PASSWORD is required}"
: "${DB_EXTRA_DATABASES:=}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$DB_OWNER_PASSWORD" -v app_pw="$DB_APP_PASSWORD" -v platform_pw="$DB_PLATFORM_PASSWORD" <<'SQL'
CREATE ROLE esabong_owner    LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE esabong_app      LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
CREATE ROLE esabong_platform LOGIN PASSWORD :'platform_pw' NOSUPERUSER NOCREATEROLE BYPASSRLS;
SQL

for db in "$DB_NAME" $DB_EXTRA_DATABASES; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE DATABASE "$db" OWNER esabong_owner;
REVOKE ALL ON DATABASE "$db" FROM PUBLIC;
GRANT CONNECT ON DATABASE "$db" TO esabong_app, esabong_platform;
SQL
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<'SQL'
ALTER SCHEMA public OWNER TO esabong_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO esabong_app, esabong_platform;
SQL
done
