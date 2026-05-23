#!/bin/sh
set -e

echo "==> Running database migrations..."
cd /app/backend
alembic upgrade head

echo "==> Starting MemoSeed backend..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
