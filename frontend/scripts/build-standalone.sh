#!/bin/bash
# Build the Next.js standalone bundle AND copy the static assets to the
# directory the standalone server actually serves from.
#
# Why this is needed
# ------------------
# next.config.ts has `output: "standalone"`, which produces a self-contained
# server in `.next/standalone/`. The server serves CSS/JS chunks from
# `.next/standalone/.next/static/`, but `next build` only writes them to
# `.next/static/`. The Dockerfile does the copy automatically; running
# `npm run build` directly on the VPS does NOT.
#
# Without this script, every rebuild leaves static assets in the wrong
# place and the browser 404s on every CSS file.
#
# Usage
# -----
#   cd /opt/MemoSeed/frontend
#   bash scripts/build-standalone.sh
#   systemctl restart memoseed-frontend

set -euo pipefail

cd "$(dirname "$0")/.."   # repo root of the frontend project

echo "[1/4] npm run build"
npm run build

echo "[2/4] copy .next/static -> .next/standalone/.next/static"
rm -rf .next/standalone/.next/static
cp -r .next/static .next/standalone/.next/static

if [ -d public ]; then
  echo "[3/4] copy public -> .next/standalone/public"
  rm -rf .next/standalone/public
  cp -r public .next/standalone/public
else
  echo "[3/4] no public/ dir, skipping"
fi

echo "[4/4] restart memoseed-frontend"
systemctl restart memoseed-frontend
sleep 2
systemctl is-active memoseed-frontend
