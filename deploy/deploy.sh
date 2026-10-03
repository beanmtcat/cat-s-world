#!/usr/bin/env bash
# Package source locally, then build and restart selected services on a remote host.
set -euo pipefail
umask 077

deploy_config_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$deploy_config_dir/.." && pwd)"
# Read only deployment settings from .env; do not execute the file as shell code.
if [[ -f "$deploy_config_dir/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^(DEPLOY_HOST|DEPLOY_DIR|DEPLOY_SSH_PORT|DEPLOY_SSH_KEY|DEPLOY_ARCHIVE_DIR|DEPLOY_COMPOSE_FILE)=(.*)$ ]]; then
      config_key="${BASH_REMATCH[1]}"
      if [[ -z "${!config_key:-}" ]]; then
        printf -v "$config_key" '%s' "${BASH_REMATCH[2]}"
      fi
    fi
  done < "$deploy_config_dir/.env"
fi
target="${1:-}"
deploy_host="${DEPLOY_HOST:-}"
deploy_dir="${DEPLOY_DIR:-}"
if [[ -z "$deploy_dir" ]]; then deploy_dir='~/docker/cat-s-world'; fi
deploy_ssh_port="${DEPLOY_SSH_PORT:-22}"
deploy_ssh_key="${DEPLOY_SSH_KEY:-}"
deploy_archive_dir="${DEPLOY_ARCHIVE_DIR:-/tmp}"
deploy_compose_file="${DEPLOY_COMPOSE_FILE:-docker-compose.yml}"

usage() {
  cat <<'EOF'
Usage: ./deploy.sh <config|frontend|api|all>

Targets:
  config    Upload production configuration and source without starting services.
  frontend  Rebuild and restart only the Nginx frontend (API must be running).
  api       Rebuild and restart only the API (dependencies must be running).
  all       Build API and frontend, then start/update them and their dependencies.

Remote configuration:
  DEPLOY_HOST          Required SSH destination in deploy/.env, e.g. deploy@example.com.
  DEPLOY_DIR           Existing remote project directory (default: ~/docker/cat-s-world).
  DEPLOY_SSH_PORT      SSH port (default: 22).
  DEPLOY_SSH_KEY       Optional path to an SSH private key.
  DEPLOY_COMPOSE_FILE  Compose file name in DEPLOY_DIR (default: docker-compose.yml).
  DEPLOY_ARCHIVE_DIR   Local temporary archive directory (default: /tmp).

The script uploads deploy/.env and deploy/docker-compose.yml to the remote project
directory as .env and DEPLOY_COMPOSE_FILE. The local development .env, data,
content, build outputs and dependencies are never uploaded. Run 'config', then
provision-production-db.sh on the server, then the database import and 'all'.
EOF
}

case "$target" in
  config|frontend|api|all) ;;
  -h|--help|"") usage; exit 0 ;;
  *) echo "Unknown deployment target: $target" >&2; usage >&2; exit 64 ;;
esac

if [[ -z "$deploy_host" ]]; then
  echo "Set DEPLOY_HOST in deploy/.env before publishing." >&2
  exit 64
fi
if [[ ! "$deploy_ssh_port" =~ ^[0-9]+$ ]] || (( deploy_ssh_port < 1 || deploy_ssh_port > 65535 )); then
  echo "DEPLOY_SSH_PORT must be between 1 and 65535." >&2
  exit 64
fi
if [[ "$deploy_compose_file" == */* || "$deploy_compose_file" == .* ]]; then
  echo "DEPLOY_COMPOSE_FILE must be a file name inside DEPLOY_DIR." >&2
  exit 64
fi
if [[ -n "$deploy_ssh_key" && ! -r "$deploy_ssh_key" ]]; then
  echo "DEPLOY_SSH_KEY does not point to a readable private key: $deploy_ssh_key" >&2
  exit 64
fi
if [[ ! -f "$deploy_config_dir/.env" || ! -f "$deploy_config_dir/docker-compose.yml" ]]; then
  echo "Create deploy/.env and deploy/docker-compose.yml before publishing." >&2
  exit 64
fi
if [[ "$target" != config ]]; then
  s3_public_base_url="$(sed -n 's/^S3_PUBLIC_BASE_URL=//p' "$deploy_config_dir/.env")"
  if [[ ! "$s3_public_base_url" =~ ^https?://[^[:space:]]+$ ]]; then
    echo "S3_PUBLIC_BASE_URL must be a complete browser-accessible http(s) URL, not an internal MinIO host." >&2
    exit 64
  fi
  docker compose --project-directory "$project_dir" --env-file "$deploy_config_dir/.env" -f "$deploy_config_dir/docker-compose.yml" config -q
fi

ssh_options=(-p "$deploy_ssh_port")
scp_options=(-P "$deploy_ssh_port" -p)
if [[ -n "$deploy_ssh_key" ]]; then
  ssh_options+=(-i "$deploy_ssh_key")
  scp_options+=(-i "$deploy_ssh_key")
fi

mkdir -p "$deploy_archive_dir"
archive_temp_dir="$(mktemp -d "$deploy_archive_dir/cat-s-world-${target}-XXXXXXXX")"
archive_name="$(basename "$archive_temp_dir").tar.gz"
archive_path="$archive_temp_dir/$archive_name"
trap 'rm -f -- "$archive_path"; rmdir -- "$archive_temp_dir"' EXIT
deploy_paths=(deploy/.env deploy/.env.example deploy/docker-compose.yml deploy/docker-compose.example.yml deploy/provision-production-db.sh)
if [[ -d "$project_dir/deploy/secrets" ]]; then
  deploy_paths+=(deploy/secrets)
fi

echo "Packaging $target locally..."
(
  cd "$project_dir"
  COPYFILE_DISABLE=1 tar -czf "$archive_path" \
    --exclude='backend/.env*' \
    --exclude='backend/.venv' \
    --exclude='backend/__pycache__' \
    --exclude='backend/.pytest_cache' \
    --exclude='frontend/.env*' \
    --exclude='frontend/node_modules' \
    --exclude='frontend/dist' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='._*' \
    --exclude='.AppleDouble' \
    .dockerignore \
    package.json package-lock.json backend frontend \
    "${deploy_paths[@]}"
)

echo "Uploading $archive_name to $deploy_host..."
scp "${scp_options[@]}" "$archive_path" "$deploy_host:/tmp/$archive_name"

remote_quote() { printf '%q' "$1"; }
remote_args="$(remote_quote "$deploy_dir") $(remote_quote "$deploy_compose_file") $(remote_quote "$target") $(remote_quote "/tmp/$archive_name")"

echo "Building and restarting $target on $deploy_host..."
ssh "${ssh_options[@]}" "$deploy_host" "bash -se -- $remote_args" <<'REMOTE'
set -euo pipefail
umask 077

deploy_dir="$1"
compose_file="$2"
target="$3"
archive_path="$4"
if [[ "$deploy_dir" == '~/'* ]]; then
  deploy_dir="$HOME/${deploy_dir#\~/}"
fi
if [[ "$deploy_dir" == / || "$deploy_dir" == "$HOME" ]]; then
  echo "DEPLOY_DIR must be a dedicated project directory." >&2
  exit 64
fi
if [[ ! -d "$deploy_dir" ]]; then
  echo "Remote DEPLOY_DIR must already exist: $deploy_dir" >&2
  exit 64
fi

stage_dir="$(mktemp -d "$deploy_dir/.deploy-XXXXXXXX")"
trap 'rm -f -- "$archive_path"; rm -rf -- "$stage_dir"' EXIT
chmod 600 "$archive_path"
tar -xzf "$archive_path" -C "$stage_dir"
tar -C "$stage_dir" -cf - \
  .dockerignore package.json package-lock.json backend frontend \
  deploy/.env.example deploy/docker-compose.example.yml | tar -C "$deploy_dir" -xf -
# Older archives may already have left macOS AppleDouble sidecars behind.
# Alembic loads every *.py in versions/, including these invalid ._*.py files.
find "$deploy_dir/backend/alembic" -type f -name '._*.py' -delete
install -m 600 "$stage_dir/deploy/.env" "$deploy_dir/.env"
install -m 600 "$stage_dir/deploy/docker-compose.yml" "$deploy_dir/$compose_file"
install -m 700 "$stage_dir/deploy/provision-production-db.sh" "$deploy_dir/provision-production-db.sh"
if [[ -d "$stage_dir/deploy/secrets" ]]; then
  tar -C "$stage_dir/deploy" -cf - secrets | tar -C "$deploy_dir" -xf -
  find "$deploy_dir/secrets" -type d -exec chmod 700 {} +
  find "$deploy_dir/secrets" -type f -exec chmod 600 {} +
fi
cd "$deploy_dir"

compose=(docker compose --env-file .env -f "$compose_file")
if [[ "$target" != config ]]; then
  "${compose[@]}" config -q
fi
case "$target" in
  config)
    echo "Production configuration uploaded; no containers were started."
    ;;
  frontend)
    "${compose[@]}" build --pull mmcat-frontend
    "${compose[@]}" up -d --no-deps --force-recreate mmcat-frontend
    "${compose[@]}" logs --tail=50 mmcat-frontend
    ;;
  api)
    "${compose[@]}" build --pull mmcat-api
    "${compose[@]}" up -d --no-deps --force-recreate mmcat-api
    "${compose[@]}" logs --tail=50 mmcat-api
    ;;
  all)
    "${compose[@]}" build --pull mmcat-api mmcat-frontend
    "${compose[@]}" up -d mmcat-api mmcat-frontend
    "${compose[@]}" logs --tail=50 mmcat-api mmcat-frontend
    ;;
esac
REMOTE
