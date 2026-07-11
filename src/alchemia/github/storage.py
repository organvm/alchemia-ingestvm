"""Persistent operational store for the BIFRONS portal.

A single local SQLite file (``~/.organvm/bifrons/portal.db``, override with
``$BIFRONS_DB``) is the portal's operational store. It is local, transactional,
portable, and needs no service — sufficient for several thousand repositories.

Table ownership (each repo owns its group's DDL; all ``CREATE ... IF NOT
EXISTS`` so they compose idempotently on the same file):

* alchemia (this module)  : bifrons_meta, external_repo, star_event,
                            repo_snapshot, artifact, dossier, exchange
* organvm-engine          : resonance_edge, transmutation_proposal,
                            contribution_candidate, upstream_interaction,
                            backflow_signal (+ the same ``exchange`` DDL so it
                            can run standalone)

The ``exchange`` table is the spine: one row per star traversal, seeded here at
intake (state STARRED/INDEXED) and advanced by the engine downstream. Its DDL
MUST stay byte-identical to the engine's copy — keep it minimal.

Privacy: private-star metadata lives only in this local store and is never
serialized into a public repository.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

# --- shared spine DDL (MUST match organvm-engine's copy verbatim) ------------
EXCHANGE_DDL = """
CREATE TABLE IF NOT EXISTS exchange (
    exchange_id            TEXT PRIMARY KEY,
    external_repo_node_id  TEXT NOT NULL,
    external_repo          TEXT NOT NULL,
    state                  TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    data_json              TEXT NOT NULL DEFAULT '{}'
)
"""

# --- alchemia-owned intake DDL ----------------------------------------------
_INTAKE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS bifrons_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_repo (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id               TEXT UNIQUE NOT NULL,
        full_name             TEXT NOT NULL,
        owner                 TEXT NOT NULL,
        name                  TEXT NOT NULL,
        url                   TEXT NOT NULL,
        owner_type            TEXT DEFAULT '',
        description           TEXT DEFAULT '',
        topics                TEXT DEFAULT '[]',
        primary_language      TEXT DEFAULT '',
        default_branch        TEXT DEFAULT 'main',
        archived              INTEGER DEFAULT 0,
        fork                  INTEGER DEFAULT 0,
        is_private            INTEGER DEFAULT 0,
        license_spdx          TEXT DEFAULT '',
        first_starred_at      TEXT DEFAULT '',
        currently_starred     INTEGER DEFAULT 1,
        materialization_level TEXT DEFAULT 'S0',
        first_seen_at         TEXT DEFAULT '',
        last_seen_at          TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS star_event (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id          TEXT NOT NULL,
        full_name        TEXT NOT NULL,
        event            TEXT NOT NULL,
        at               TEXT NOT NULL,
        exchange_id      TEXT DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repo_snapshot (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id          TEXT NOT NULL,
        full_name        TEXT NOT NULL,
        ref              TEXT NOT NULL,
        snapshot_at      TEXT NOT NULL,
        pushed_at        TEXT DEFAULT '',
        stargazers_count INTEGER DEFAULT 0,
        open_issue_count INTEGER DEFAULT 0,
        state_json       TEXT DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id       TEXT NOT NULL,
        full_name     TEXT NOT NULL,
        kind          TEXT NOT NULL,
        name          TEXT NOT NULL,
        source_url    TEXT DEFAULT '',
        ref           TEXT DEFAULT '',
        fetched_at    TEXT NOT NULL,
        sha256        TEXT NOT NULL,
        bytes_len     INTEGER DEFAULT 0,
        truncated     INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dossier (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id        TEXT NOT NULL,
        full_name      TEXT NOT NULL,
        level          TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        snapshot_ref   TEXT DEFAULT '',
        snapshot_at    TEXT DEFAULT '',
        doc_json       TEXT NOT NULL,
        exchange_id    TEXT DEFAULT '',
        UNIQUE(node_id, level)
    )
    """,
]


def now_iso() -> str:
    """UTC ISO-8601 timestamp (seconds precision, Z-suffixed)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    """Resolve the portal DB path (``$BIFRONS_DB`` overrides the default)."""
    env = os.environ.get("BIFRONS_DB")
    if env:
        return Path(env).expanduser()
    return Path("~/.organvm/bifrons/portal.db").expanduser()


def new_exchange_id() -> str:
    """Mint a time-sortable exchange id (``ex_<ms-hex><rand-hex>``)."""
    ms = int(time.time() * 1000)
    return f"ex_{ms:012x}{os.urandom(4).hex()}"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating parent dirs) the portal DB in WAL mode."""
    db_path = Path(path).expanduser() if path else default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_intake_schema(conn: sqlite3.Connection) -> None:
    """Create the alchemia-owned intake tables + the shared exchange spine."""
    for ddl in _INTAKE_DDL:
        conn.execute(ddl)
    conn.execute(EXCHANGE_DDL)
    set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()


# --- meta --------------------------------------------------------------------
def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO bifrons_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM bifrons_meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# --- external_repo -----------------------------------------------------------
def get_external_repo(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM external_repo WHERE node_id=?",
        (node_id,),
    ).fetchone()


def get_external_repo_by_full_name(
    conn: sqlite3.Connection,
    full_name: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM external_repo WHERE full_name=?",
        (full_name,),
    ).fetchone()


def repos_needing_dossier(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Currently-starred public repos still at S0 (no dossier yet)."""
    sql = (
        "SELECT * FROM external_repo WHERE currently_starred=1 "
        "AND is_private=0 AND materialization_level='S0' ORDER BY full_name"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def upsert_external_repo(conn: sqlite3.Connection, repo: Any, *, seen_at: str) -> bool:
    """Insert or update an external_repo row. Returns True if it was new."""
    import json

    existing = get_external_repo(conn, repo.node_id)
    if existing is None:
        conn.execute(
            """
            INSERT INTO external_repo(
                node_id, full_name, owner, name, url, owner_type, description,
                topics, primary_language, default_branch, archived, fork,
                is_private, license_spdx, first_starred_at, currently_starred,
                materialization_level, first_seen_at, last_seen_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'S0',?,?)
            """,
            (
                repo.node_id, repo.full_name, repo.owner, repo.name, repo.url,
                repo.owner_type, repo.description, json.dumps(repo.topics),
                repo.primary_language, repo.default_branch, int(repo.archived),
                int(repo.fork), int(repo.is_private), repo.license_spdx,
                repo.starred_at, seen_at, seen_at,
            ),
        )
        return True
    conn.execute(
        """
        UPDATE external_repo SET
            full_name=?, owner=?, name=?, url=?, owner_type=?, description=?,
            topics=?, primary_language=?, default_branch=?, archived=?, fork=?,
            is_private=?, license_spdx=?, currently_starred=1, last_seen_at=?
        WHERE node_id=?
        """,
        (
            repo.full_name, repo.owner, repo.name, repo.url, repo.owner_type,
            repo.description, json.dumps(repo.topics), repo.primary_language,
            repo.default_branch, int(repo.archived), int(repo.fork),
            int(repo.is_private), repo.license_spdx, seen_at, repo.node_id,
        ),
    )
    return False


def mark_unstarred(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute(
        "UPDATE external_repo SET currently_starred=0 WHERE node_id=?",
        (node_id,),
    )


def list_external_repos(
    conn: sqlite3.Connection,
    *,
    currently_starred: bool | None = True,
) -> list[sqlite3.Row]:
    if currently_starred is None:
        return conn.execute("SELECT * FROM external_repo ORDER BY full_name").fetchall()
    return conn.execute(
        "SELECT * FROM external_repo WHERE currently_starred=? ORDER BY full_name",
        (int(currently_starred),),
    ).fetchall()


def set_materialization_level(conn: sqlite3.Connection, node_id: str, level: str) -> None:
    conn.execute(
        "UPDATE external_repo SET materialization_level=? WHERE node_id=?",
        (level, node_id),
    )


# --- star_event --------------------------------------------------------------
def record_star_event(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    full_name: str,
    event: str,
    at: str,
    exchange_id: str = "",
) -> None:
    conn.execute(
        "INSERT INTO star_event(node_id, full_name, event, at, exchange_id) "
        "VALUES(?,?,?,?,?)",
        (node_id, full_name, event, at, exchange_id),
    )


# --- exchange (spine) --------------------------------------------------------
def seed_exchange(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    full_name: str,
    state: str = "STARRED",
) -> str:
    """Create the exchange spine row for a star and return its exchange_id."""
    exchange_id = new_exchange_id()
    ts = now_iso()
    conn.execute(
        "INSERT INTO exchange(exchange_id, external_repo_node_id, external_repo, "
        "state, created_at, updated_at, data_json) VALUES(?,?,?,?,?,?, '{}')",
        (exchange_id, node_id, full_name, state, ts, ts),
    )
    return exchange_id


def exchange_for_repo(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM exchange WHERE external_repo_node_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (node_id,),
    ).fetchone()


# --- snapshots / artifacts / dossiers ---------------------------------------
def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    full_name: str,
    ref: str,
    pushed_at: str = "",
    stargazers_count: int = 0,
    open_issue_count: int = 0,
    state_json: str = "{}",
) -> int:
    cur = conn.execute(
        "INSERT INTO repo_snapshot(node_id, full_name, ref, snapshot_at, "
        "pushed_at, stargazers_count, open_issue_count, state_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (node_id, full_name, ref, now_iso(), pushed_at, stargazers_count,
         open_issue_count, state_json),
    )
    return int(cur.lastrowid or 0)


def insert_artifact(conn: sqlite3.Connection, node_id: str, full_name: str, art: Any) -> None:
    conn.execute(
        "INSERT INTO artifact(node_id, full_name, kind, name, source_url, ref, "
        "fetched_at, sha256, bytes_len, truncated) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (node_id, full_name, art.kind, art.name, art.source_url, art.ref,
         art.fetched_at, art.sha256, art.bytes_len, int(art.truncated)),
    )


def upsert_dossier(conn: sqlite3.Connection, dossier: Any, *, exchange_id: str = "") -> None:
    import json

    conn.execute(
        """
        INSERT INTO dossier(node_id, full_name, level, schema_version,
            snapshot_ref, snapshot_at, doc_json, exchange_id)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(node_id, level) DO UPDATE SET
            doc_json=excluded.doc_json, snapshot_ref=excluded.snapshot_ref,
            snapshot_at=excluded.snapshot_at, exchange_id=excluded.exchange_id
        """,
        (
            dossier.github_node_id, dossier.external_repo, dossier.level,
            dossier.schema_version, dossier.snapshot_ref, dossier.snapshot_at,
            json.dumps(dossier.to_dict()), exchange_id,
        ),
    )


def get_dossier(
    conn: sqlite3.Connection,
    node_id: str,
    level: str | None = None,
) -> sqlite3.Row | None:
    if level:
        return conn.execute(
            "SELECT * FROM dossier WHERE node_id=? AND level=?",
            (node_id, level),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM dossier WHERE node_id=? ORDER BY level DESC LIMIT 1",
        (node_id,),
    ).fetchone()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Summary row counts for status reporting."""
    out: dict[str, int] = {}
    for table in ("external_repo", "star_event", "repo_snapshot", "artifact",
                  "dossier", "exchange"):
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608
        out[table] = int(row["n"])
    starred = conn.execute(
        "SELECT COUNT(*) AS n FROM external_repo WHERE currently_starred=1",
    ).fetchone()
    out["currently_starred"] = int(starred["n"])
    return out
