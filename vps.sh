#!/usr/bin/env bash
# Quick SSH to MemoSeed VPS
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY="${MEMOSEED_SSH_KEY:-${SCRIPT_DIR}/.vps_key}"
VPS="root@8.148.221.17"

if [ $# -eq 0 ]; then
  exec ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${VPS}"
else
  exec ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${VPS}" "$@"
fi
