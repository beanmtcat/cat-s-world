#!/usr/bin/env bash
# Export the local PostgreSQL database and restore it into a fresh database
# provisioned on an existing production PostgreSQL server.
# This script is intentionally separate from deploy.sh: database import is one-time.
set -euo pipefail
umask 077

deploy_config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$deploy_config_dir/.." && pwd)"
# Read only connection/import settings; .env may contain passwords with shell metacharacters.
if [[ -f "$deploy_config_dir/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^(DEPLOY_HOST|DEPLOY_DIR|DEPLOY_SSH_PORT|DEPLOY_SSH_KEY|DEPLOY_COMPOSE_FILE|LOCAL_COMPOSE_FILE|DB_BACKUP_DIR|CONFIRM_PRODUCTION_DB_IMPORT)=(.*)$ ]]; then
      config_key="${BASH_REMATCH[1]}"
      if [[ -z "${!config_key:-}" ]]; then
        printf -v "$config_key" '%s' "${BASH_REMATCH[2]}"
      fi
    fi
  done < "$deploy_config_dir/.env"
fi
deploy_host="${DEPLOY_HOST:-}"
deploy_dir="${DEPLOY_DIR:-}"
if [[ -z "$deploy_dir" ]]; then deploy_dir='~/docker/cat-s-world'; fi
deploy_ssh_port="${DEPLOY_SSH_PORT:-22}"
deploy_ssh_key="${DEPLOY_SSH_KEY:-}"
deploy_compose_file="${DEPLOY_COMPOSE_FILE:-docker-compose.yml}"
local_compose_file="${LOCAL_COMPOSE_FILE:-docker-compose.yml}"
backup_dir="${DB_BACKUP_DIR:-$project_dir/content/backups}"

usage() {
  cat <<'EOF'
Usage: ./migrate-production-db.sh

Exports the local Compose postgres database to a private custom-format dump,
uploads it over SSH, and restores it only when the target database contains no
public tables. It does not create a database or start
the API or frontend, delete an existing database, or migrate MinIO objects.

Configuration:
  CONFIRM_PRODUCTION_DB_IMPORT  Must be YES in deploy/.env.
  DEPLOY_HOST          Required SSH destination.
  DEPLOY_DIR           Existing remote project directory (default: ~/docker/cat-s-world).
  DEPLOY_SSH_PORT      SSH port (default: 22).
  DEPLOY_SSH_KEY       Optional SSH private key path.
  DEPLOY_COMPOSE_FILE  Remote Compose file (default: docker-compose.yml).
  LOCAL_COMPOSE_FILE   Local Compose file (default: docker-compose.yml).
  DB_BACKUP_DIR        Where to retain the private local dump (default: content/backups).

First run ./deploy.sh config, then provision-production-db.sh on the server
with a PostgreSQL administrator login. The remote directory must have a
production .env and Compose file before importing.
The production API must not be running during import. After a successful
restore, run ./deploy.sh all from this local deploy directory.
EOF
}

if [[ "${1:-}" == '-h' || "${1:-}" == '--help' ]]; then usage; exit 0; fi
if (( $# != 0 )); then usage >&2; exit 64; fi
if [[ "${CONFIRM_PRODUCTION_DB_IMPORT:-}" != YES ]]; then
  echo "Set CONFIRM_PRODUCTION_DB_IMPORT=YES in deploy/.env to acknowledge the production write." >&2
  exit 64
fi
if [[ -z "$deploy_host" ]]; then
  echo "DEPLOY_HOST is required." >&2
  exit 64
fi
if [[ ! "$deploy_ssh_port" =~ ^[0-9]+$ ]] || (( deploy_ssh_port < 1 || deploy_ssh_port > 65535 )); then
  echo "DEPLOY_SSH_PORT must be between 1 and 65535." >&2
  exit 64
fi
if [[ "$deploy_compose_file" == */* || "$deploy_compose_file" == .* || "$local_compose_file" == */* || "$local_compose_file" == .* ]]; then
  echo "Compose file options must be file names in their respective project directories." >&2
  exit 64
fi
if [[ -n "$deploy_ssh_key" && ! -r "$deploy_ssh_key" ]]; then
  echo "DEPLOY_SSH_KEY is not a readable file: $deploy_ssh_key" >&2
  exit 64
fi
if [[ ! -f "$project_dir/$local_compose_file" ]]; then
  echo "Local Compose file not found: $project_dir/$local_compose_file" >&2
  exit 64
fi

ssh_options=(-p "$deploy_ssh_port")
scp_options=(-P "$deploy_ssh_port" -p)
if [[ -n "$deploy_ssh_key" ]]; then
  ssh_options+=(-i "$deploy_ssh_key")
  scp_options+=(-i "$deploy_ssh_key")
fi

mkdir -p "$backup_dir"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup_path="$backup_dir/cat-s-world-${timestamp}-$$.dump"
partial_path="$(mktemp "$backup_dir/.postgres-XXXXXXXX")"
trap 'rm -f -- "$partial_path"' EXIT

echo "Exporting local PostgreSQL to $backup_path..."
(
  cd "$project_dir"
  docker compose -f "$local_compose_file" exec -T postgres sh -ec '
    export PGPASSWORD="$POSTGRES_PASSWORD"
    exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl
  ' > "$partial_path"
)
if [[ ! -s "$partial_path" ]]; then
  echo "The local database dump is empty; refusing to upload." >&2
  exit 1
fi
mv -- "$partial_path" "$backup_path"
backup_sha256="$(shasum -a 256 "$backup_path" | awk '{print $1}')"
echo "Local database backup retained at: $backup_path"

remote_dump="/tmp/$(basename "$backup_path")"
echo "Uploading encrypted in transit to $deploy_host..."
scp "${scp_options[@]}" "$backup_path" "$deploy_host:$remote_dump"

remote_quote() { printf '%q' "$1"; }
remote_args="$(remote_quote "$deploy_dir") $(remote_quote "$deploy_compose_file") $(remote_quote "$remote_dump") $(remote_quote "$backup_sha256")"

echo "Checking and restoring the new production database..."
ssh "${ssh_options[@]}" "$deploy_host" "bash -se -- $remote_args" <<'REMOTE'
set -euo pipefail
umask 077
deploy_dir="$1"
compose_file="$2"
dump_path="$3"
expected_sha256="$4"
trap 'rm -f -- "$dump_path"' EXIT
chmod 600 "$dump_path"
actual_sha256="$(sha256sum "$dump_path" | awk '{print $1}')"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
  echo "Uploaded database dump checksum mismatch; refusing to restore." >&2
  exit 1
fi

if [[ "$deploy_dir" == '~/'* ]]; then
  deploy_dir="$HOME/${deploy_dir#\~/}"
fi
if [[ "$deploy_dir" == / || "$deploy_dir" == "$HOME" || ! -f "$deploy_dir/.env" || ! -f "$deploy_dir/$compose_file" ]]; then
  echo "DEPLOY_DIR must be a dedicated project directory with .env and the Compose file." >&2
  exit 64
fi
cd "$deploy_dir"
if [[ "$(docker inspect -f '{{.State.Running}}' mmcat-api 2>/dev/null || true)" == true ]]; then
  echo "The production API is running. Stop it before importing the database." >&2
  exit 1
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" =~ ^(DB_HOST|DB_CONTAINER_NAME|DB_CONTAINER_PORT|POSTGRES_USER|POSTGRES_DB|POSTGRES_PASSWORD)=(.*)$ ]]; then
    printf -v "${BASH_REMATCH[1]}" '%s' "${BASH_REMATCH[2]}"
  fi
done < .env
: "${DB_HOST:?Set DB_HOST in the production .env}"
: "${DB_CONTAINER_NAME:?Set DB_CONTAINER_NAME in the production .env}"
: "${DB_CONTAINER_PORT:=5432}"
: "${POSTGRES_USER:?Set POSTGRES_USER in the production .env}"
: "${POSTGRES_DB:?Set POSTGRES_DB in the production .env}"
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in the production .env}"
if [[ ! "$DB_CONTAINER_PORT" =~ ^[0-9]+$ ]] || (( DB_CONTAINER_PORT < 1 || DB_CONTAINER_PORT > 65535 )); then
  echo "DB_CONTAINER_PORT must be between 1 and 65535." >&2
  exit 64
fi
if [[ "$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER_NAME" 2>/dev/null)" != true ]]; then
  echo "The configured PostgreSQL container is not running: $DB_CONTAINER_NAME" >&2
  exit 1
fi
export PGHOST=127.0.0.1 PGPORT="$DB_CONTAINER_PORT" PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PASSWORD"
db_exec() {
  docker exec -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD \
    "$DB_CONTAINER_NAME" "$@"
}
restore_client() {
  # The source pg_dump is PostgreSQL 16 while the existing server uses 15.
  # A temporary client container shares the database container's network,
  # but does not start or replace a PostgreSQL server.
  docker run --rm --network "container:$DB_CONTAINER_NAME" -i \
    -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD \
    postgres:16-alpine "$@"
}
db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc 'SELECT 1' | grep -qx 1 || {
  echo "Cannot connect as the application user. Provision its role and database first." >&2
  exit 1
}
table_count="$(db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc \
  "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'")"
if [[ "$table_count" != 0 ]]; then
  echo "The production database already has $table_count public tables; refusing to overwrite it." >&2
  exit 1
fi

echo "Checking the dump with a PostgreSQL 16 restore client..."
restore_client pg_restore -Fc --list < "$dump_path" >/dev/null
echo "Restoring into the existing PostgreSQL server in one transaction..."
restore_client pg_restore -Fc --exit-on-error --single-transaction --no-owner --no-acl \
  -d "$POSTGRES_DB" < "$dump_path"

db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc \
  "SELECT 'posts', count(*) FROM mmcat_posts UNION ALL SELECT 'media', count(*) FROM mmcat_media UNION ALL SELECT 'comments', count(*) FROM mmcat_comments;"
db_exec psql -X -v ON_ERROR_STOP=1 -d "$POSTGRES_DB" -Atqc "SELECT version_num FROM alembic_version;"
echo "Production database import completed. Run ./deploy.sh all from the local deploy directory."
REMOTE
