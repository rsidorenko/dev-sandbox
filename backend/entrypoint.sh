#!/bin/bash
set -e

echo "Waiting for PostgreSQL..."
until python -c "
import asyncio, asyncpg
async def _check():
    conn = await asyncpg.connect('$DATABASE_URL', timeout=3)
    await conn.close()
asyncio.run(_check())
" 2>/dev/null; do
  sleep 1
done
echo "PostgreSQL is ready."

echo "Applying migrations..."
python -m app.persistence.postgres_migrations_main
echo "Migrations applied."

exec uvicorn app.runtime.telegram_webhook_main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --log-level info
