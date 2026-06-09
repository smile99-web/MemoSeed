#!/usr/bin/env bash
# MemoSeed VPS Sync Script
# Syncs local code changes to VPS and restarts affected services.
#
# Usage:
#   ./sync.sh              # full sync both backend + frontend
#   ./sync.sh backend      # sync only backend
#   ./sync.sh frontend     # sync only frontend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VPS_HOST="8.148.221.17"
VPS_USER="root"
VPS_PATH="/opt/MemoSeed"
SSH_KEY="${MEMOSEED_SSH_KEY:-${SCRIPT_DIR}/.vps_key}"

SSH_CMD="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"
RSYNC_RSH="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"

# Common rsync excludes
EXCLUDES=(
  --exclude '.git'
  --exclude '.env'
  --exclude '.venv'
  --exclude 'node_modules'
  --exclude '.next'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude 'tts_cache'
  --exclude 'docker'
  --exclude '.dockerignore'
  --exclude 'docker-compose.yml'
  --exclude 'docker-compose.prod.yml'
  --exclude '.vps_key'
  --exclude 'sync.sh'
  --exclude 'vps.sh'
)

say() { echo -e "\n\033[1;34m==>\033[0m $*"; }
ok()  { echo -e "\033[1;32m  OK\033[0m $*"; }
err() { echo -e "\033[1;31m  ERR\033[0m $*"; exit 1; }

run_vps() {
  ${SSH_CMD} "${VPS_USER}@${VPS_HOST}" "$@"
}

sync_files() {
  local extra_excludes=("$@")
  rsync -avz --delete \
    "${EXCLUDES[@]}" \
    "${extra_excludes[@]}" \
    -e "${RSYNC_RSH}" \
    "${SCRIPT_DIR}/" \
    "${VPS_USER}@${VPS_HOST}:${VPS_PATH}/"
}

sync_backend() {
  say "Syncing backend code..."
  sync_files --exclude 'frontend' --exclude 'database' --exclude 'prompts' --exclude 'docs'
  ok "Backend files synced"

  say "Restarting backend..."
  run_vps "systemctl restart memoseed-backend && systemctl status memoseed-backend --no-pager -l | head -8"
  ok "Backend restarted"
}

sync_frontend() {
  say "Syncing frontend code..."
  sync_files --exclude 'backend' --exclude 'database' --exclude 'prompts' --exclude 'docs'
  ok "Frontend files synced"

  say "Installing dependencies..."
  run_vps "cd ${VPS_PATH}/frontend && npm install --production 2>&1 | tail -5"
  ok "Dependencies checked"

  say "Building Next.js..."
  run_vps "cd ${VPS_PATH}/frontend && npm run build 2>&1 | tail -10"
  ok "Next.js built"

  say "Restarting frontend..."
  run_vps "systemctl restart memoseed-frontend && systemctl status memoseed-frontend --no-pager -l | head -8"
  ok "Frontend restarted"
}

sync_full() {
  say "Full sync: all project files..."
  sync_files --exclude nobody-uses-this-dir
  ok "All files synced"

  say "Restarting backend..."
  run_vps "systemctl restart memoseed-backend"
  ok "Backend restarted"

  say "Installing frontend dependencies..."
  run_vps "cd ${VPS_PATH}/frontend && npm install --production 2>&1 | tail -5"

  say "Building Next.js..."
  run_vps "cd ${VPS_PATH}/frontend && npm run build 2>&1 | tail -10"

  say "Restarting frontend..."
  run_vps "systemctl restart memoseed-frontend"
  ok "Frontend restarted"
}

# --- Main ---
if [ ! -f "${SSH_KEY}" ]; then
  err "SSH key not found at ${SSH_KEY}. Set MEMOSEED_SSH_KEY or place key at ${SCRIPT_DIR}/.vps_key"
fi

case "${1:-full}" in
  backend)  sync_backend ;;
  frontend) sync_frontend ;;
  full)     sync_full ;;
  *)
    echo "Usage: $0 [backend|frontend|full]"
    exit 1
    ;;
esac

say "Sync complete!"
