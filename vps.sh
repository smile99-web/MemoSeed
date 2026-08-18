#!/usr/bin/env bash
# Quick SSH to the MemoSeed VPS.
# 2026-08-18: the old hardcoded IP pointed at the DECOMMISSIONED server —
# export the host explicitly: MEMOSEED_VPS_HOST=198.23.236.185 ./vps.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY="${MEMOSEED_SSH_KEY:-${SCRIPT_DIR}/.vps_key}"
VPS="root@${MEMOSEED_VPS_HOST:-}"

if [ -z "${MEMOSEED_VPS_HOST:-}" ]; then
  echo "ERROR: MEMOSEED_VPS_HOST is not set (e.g. MEMOSEED_VPS_HOST=198.23.236.185 $0)" >&2
  exit 1
fi

if [ $# -eq 0 ]; then
  exec ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${VPS}"
else
  exec ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${VPS}" "$@"
fi
