#!/usr/bin/env bash
# Run on the production server with an existing PostgreSQL administrator login.
# Administrator credentials are read from DB_ADMIN_USER/DB_ADMIN_PASSWORD in
# the production .env. Clear the administrator password after provisioning.
set -euo pipefail
umask 077

config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$config_dir/.env" ]]; then
  echo "Production .env not found beside this script." >&2
  exit 64
fi
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" =~ ^(DB_HOST|DB_CONTAINER_NAME|DB_CONTAINER_PORT|POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD|DB_ADMIN_USER|DB_ADMIN_PASSWORD)=(.*)$ ]]; then
    printf -v "${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
  fi
done < "$config_dir/.env"

# Compose accepts single-quoted literal values; remove those quotes for libpq.
if [[ "${DB_ADMIN_PASSWORD:-}" == "'"*"'" ]]; then
  DB_ADMIN_PASSWORD="${DB_ADMIN_PASSWORD:1:${#DB_ADMIN_PASSWORD}-2}"
fi

: "${DB_HOST:?Set DB_HOST in .env to the existing PostgreSQL server}"
: "${DB_CONTAINER_NAME:?Set DB_CONTAINER_NAME in .env to the existing PostgreSQL container name}"
: "${DB_CONTAINER_PORT:=5432}"
: "${POSTGRES_USER:?Set POSTGRES_USER in .env}"
: "${POSTGRES_DB:?Set POSTGRES_DB in .env}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}"
: "${DB_ADMIN_USER:?Set DB_ADMIN_USER in .env to the existing PostgreSQL administrator}"
: "${DB_ADMIN_PASSWORD:?Set DB_ADMIN_PASSWORD in .env to that administrator password}"

if [[ ! "$POSTGRES_USER" =~ ^[a-z_][a-z0-9_]*$ || ! "$POSTGRES_DB" =~ ^[a-z_][a-z0-9_]*$ || ! "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9]+$ ]]; then
  echo "Database name/user must be simple lowercase identifiers and password must be alphanumeric." >&2
  exit 64
fi
if [[ ! "$DB_CONTAINER_PORT" =~ ^[0-9]+$ ]] || (( DB_CONTAINER_PORT < 1 || DB_CONTAINER_PORT > 65535 )); then
  echo "DB_CONTAINER_PORT must be between 1 and 65535." >&2
  exit 64
fi
command -v docker >/dev/null || { echo "Docker is required to use the existing PostgreSQL container." >&2; exit 69; }
if [[ "$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER_NAME" 2>/dev/null)" != true ]]; then
  echo "The configured PostgreSQL container is not running: $DB_CONTAINER_NAME" >&2
  exit 1
fi

export PGHOST=127.0.0.1 PGPORT="$DB_CONTAINER_PORT"
export PGUSER="$DB_ADMIN_USER" PGPASSWORD="$DB_ADMIN_PASSWORD"
db_exec() {
  docker exec -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD \
    "$DB_CONTAINER_NAME" "$@"
}
admin_psql=(db_exec psql -X -v ON_ERROR_STOP=1 -d postgres)
role_exists="$("${admin_psql[@]}" -Atqc "SELECT 1 FROM pg_roles WHERE rolname = '$POSTGRES_USER'")"
db_owner="$("${admin_psql[@]}" -Atqc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$POSTGRES_DB'")"

if [[ -n "$role_exists" || -n "$db_owner" ]]; then
  if [[ "$role_exists" != 1 || "$db_owner" != "$POSTGRES_USER" ]]; then
    echo "An existing role or database conflicts with the requested names; no changes made." >&2
    exit 1
  fi
  echo "The dedicated role and database already exist. Checking application login..."
else
  echo "Creating dedicated role and database on the existing PostgreSQL server..."
  "${admin_psql[@]}" -q -c "CREATE ROLE \"$POSTGRES_USER\" LOGIN PASSWORD '$POSTGRES_PASSWORD'"
  db_exec createdb --owner="$POSTGRES_USER" -- "$POSTGRES_DB"
fi
unset PGPASSWORD DB_ADMIN_PASSWORD

(
  export PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD"
  db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc 'SELECT 1' | grep -qx 1
) || { echo "Application role cannot connect with the configured password; check PostgreSQL authentication/network access." >&2; exit 1; }

table_count="$(
  PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD" \
    db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc \
    "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
)"
if [[ "$table_count" != 0 ]]; then
  echo "Target database contains $table_count public tables. Refusing to treat it as a fresh import target." >&2
  exit 1
fi
echo "Dedicated database and user are ready; target database is empty."
