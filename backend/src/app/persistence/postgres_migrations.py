"""Forward-only helpers to apply plain SQL migration files with asyncpg."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol


class AsyncSqlExecutor(Protocol):
    """Subset of asyncpg connection/pool API used by migration apply."""

    async def execute(self, query: str, *args: object, **kwargs: object) -> str: ...
    async def fetch(self, query: str, *args: object, **kwargs: object) -> list[Mapping[str, object]]: ...


class MigrationLedgerDriftError(RuntimeError):
    """Raised when an on-disk migration file no longer matches the ledger checksum."""


def default_migrations_directory() -> Path:
    """Return ``<backend_root>/migrations`` (this file lives under ``backend/src/...``)."""

    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / "migrations"


def sorted_migration_sql_paths(migrations_directory: Path) -> list[Path]:
    """Return ``*.sql`` files directly under ``migrations_directory``, sorted by filename."""

    if not migrations_directory.is_dir():
        msg = f"Not a directory: {migrations_directory}"
        raise NotADirectoryError(msg)
    paths = [p for p in migrations_directory.iterdir() if p.is_file() and p.suffix.lower() == ".sql"]
    paths.sort(key=lambda p: p.name)
    return paths


def _migration_sql_checksum(sql_text: str) -> str:
    return hashlib.sha256(sql_text.encode("utf-8")).hexdigest()


async def apply_postgres_migrations(
    executor: AsyncSqlExecutor,
    *,
    migrations_directory: Path | None = None,
) -> None:
    """Read each ``*.sql`` in order and run it as a single ``execute`` call.

    Applied migrations are recorded in ``schema_migration_ledger`` with a SHA-256
    checksum of the UTF-8 migration body. Re-running with identical content is a
    no-op; changing a file after apply fails fast (drift / invariant violation).
    """

    directory = migrations_directory if migrations_directory is not None else default_migrations_directory()

    await executor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_ledger (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            checksum TEXT
        )
        """.strip()
    )
    await executor.execute("ALTER TABLE schema_migration_ledger ADD COLUMN IF NOT EXISTS checksum TEXT")
    applied_rows = await executor.fetch("SELECT filename, checksum FROM schema_migration_ledger")
    ledger_checksums: dict[str, str | None] = {}
    for row in applied_rows:
        name = str(row["filename"])
        raw = row.get("checksum")
        ledger_checksums[name] = None if raw is None else str(raw)

    for path in sorted_migration_sql_paths(directory):
        filename = path.name
        sql_text = path.read_text(encoding="utf-8")
        expected_checksum = _migration_sql_checksum(sql_text)
        if filename not in ledger_checksums:
            pass  # apply below
        else:
            stored_checksum = ledger_checksums[filename]
            if stored_checksum is None:
                await executor.execute(
                    """
                    UPDATE schema_migration_ledger
                    SET checksum = $2
                    WHERE filename = $1 AND checksum IS NULL
                    """.strip(),
                    filename,
                    expected_checksum,
                )
                ledger_checksums[filename] = expected_checksum
                continue
            if stored_checksum == expected_checksum:
                continue
            msg = (
                f"Migration ledger drift for {filename!r}: checksum mismatch (ledger vs filesystem). Refusing to apply."
            )
            raise MigrationLedgerDriftError(msg)
        await executor.execute(sql_text)
        await executor.execute(
            """
            INSERT INTO schema_migration_ledger(filename, checksum)
            VALUES ($1, $2)
            ON CONFLICT (filename) DO NOTHING
            """.strip(),
            filename,
            expected_checksum,
        )
        ledger_checksums[filename] = expected_checksum
