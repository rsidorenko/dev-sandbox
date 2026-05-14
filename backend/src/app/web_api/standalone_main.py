"""Standalone Web API server for local development (no Telegram webhook)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import uvicorn
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from app.web_api.app import build_web_api_app


async def _start() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    app = build_web_api_app(pool=pool)

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(_start())
