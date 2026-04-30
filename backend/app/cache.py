from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    sha256              TEXT PRIMARY KEY,
    source_url          TEXT NOT NULL,
    html                TEXT NOT NULL,
    outline_json        TEXT NOT NULL,
    file_search_store   TEXT,
    page_count          INTEGER NOT NULL,
    title               TEXT,
    description         TEXT,
    published_at        TEXT,
    created_at          INTEGER NOT NULL,
    format              TEXT,
    render_mode         TEXT,
    pipeline_version    TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcripts_source_url
  ON transcripts(source_url);

CREATE TABLE IF NOT EXISTS url_aliases (
    source_url   TEXT PRIMARY KEY,
    sha256       TEXT NOT NULL REFERENCES transcripts(sha256) ON DELETE CASCADE,
    created_at   INTEGER NOT NULL
);
"""

# Additive migrations. Each entry is (column_name, DDL fragment). Applied at
# init_schema time for pre-existing databases that were created before these
# columns existed; no-ops for fresh installs where SCHEMA already includes them.
_ADDITIVE_COLUMNS = (
    ("format", "TEXT"),
    ("render_mode", "TEXT"),
    ("pipeline_version", "TEXT"),
)


@dataclass
class Transcript:
    sha256: str
    source_url: str
    html: str
    outline: list[dict[str, Any]]
    file_search_store: str | None
    page_count: int
    title: str | None
    description: str | None
    published_at: str | None
    created_at: int
    format: str = "pdf"
    render_mode: str = "static"
    pipeline_version: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Transcript:
        # `format` and `render_mode` default when the row pre-dates the
        # migration (everything before the DOCX/XLSX spike was PDF + static).
        keys = row.keys()
        fmt = row["format"] if "format" in keys and row["format"] else "pdf"
        mode = (
            row["render_mode"]
            if "render_mode" in keys and row["render_mode"]
            else "static"
        )
        pipeline_version = (
            row["pipeline_version"]
            if "pipeline_version" in keys and row["pipeline_version"]
            else ""
        )
        return cls(
            sha256=row["sha256"],
            source_url=row["source_url"],
            html=row["html"],
            outline=json.loads(row["outline_json"]),
            file_search_store=row["file_search_store"],
            page_count=row["page_count"],
            title=row["title"],
            description=row["description"],
            published_at=row["published_at"],
            created_at=row["created_at"],
            format=fmt,
            render_mode=mode,
            pipeline_version=pipeline_version,
        )


async def init_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        # Additive migration for DBs that existed before `format`/`render_mode`
        # were introduced. ALTER TABLE ADD COLUMN is idempotent once we filter
        # by the existing column list.
        async with db.execute("PRAGMA table_info(transcripts)") as cur:
            existing = {row[1] async for row in cur}
        for name, ddl in _ADDITIVE_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE transcripts ADD COLUMN {name} {ddl}")
        await db.commit()


async def get_by_hash(db_path: Path, sha256: str) -> Transcript | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM transcripts WHERE sha256 = ?", (sha256,)
        ) as cur:
            row = await cur.fetchone()
            return Transcript.from_row(row) if row else None


async def get_by_source_url(db_path: Path, source_url: str) -> Transcript | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT t.* FROM transcripts t
            JOIN url_aliases a ON a.sha256 = t.sha256
            WHERE a.source_url = ?
            """,
            (source_url,),
        ) as cur:
            row = await cur.fetchone()
            return Transcript.from_row(row) if row else None


async def put(
    db_path: Path,
    *,
    sha256: str,
    source_url: str,
    html: str,
    outline: list[dict[str, Any]],
    file_search_store: str | None,
    page_count: int,
    title: str | None = None,
    description: str | None = None,
    published_at: str | None = None,
    format: str = "pdf",
    render_mode: str = "static",
    pipeline_version: str = "",
) -> None:
    now = int(time.time())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO transcripts (
                sha256, source_url, html, outline_json, file_search_store,
                page_count, title, description, published_at, created_at,
                format, render_mode, pipeline_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                source_url = excluded.source_url,
                html = excluded.html,
                outline_json = excluded.outline_json,
                file_search_store = excluded.file_search_store,
                page_count = excluded.page_count,
                title = excluded.title,
                description = excluded.description,
                published_at = excluded.published_at,
                format = excluded.format,
                render_mode = excluded.render_mode,
                pipeline_version = excluded.pipeline_version
            """,
            (
                sha256,
                source_url,
                html,
                json.dumps(outline),
                file_search_store,
                page_count,
                title,
                description,
                published_at,
                now,
                format,
                render_mode,
                pipeline_version,
            ),
        )
        await db.execute(
            """
            INSERT INTO url_aliases (source_url, sha256, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET sha256 = excluded.sha256
            """,
            (source_url, sha256, now),
        )
        await db.commit()


async def list_cached(db_path: Path, limit: int = 100) -> list[Transcript]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [Transcript.from_row(r) for r in rows]
