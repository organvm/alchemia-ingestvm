"""Incremental star synchronization for BIFRONS.

Enumerates the authenticated user's starred repositories and reconciles them
against the portal store: new stars are inserted (and their exchange spine is
seeded), returning stars are refreshed, and repositories that are no longer
starred are marked ``currently_starred = 0`` with an ``unstar`` event.

Re-running a sync with no upstream change makes no row changes (idempotent).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from alchemia.github import storage
from alchemia.github.client import STAR_ACCEPT, authenticated_login, gh_api_paginated
from alchemia.github.models import StarredRepo


@dataclass
class SyncSummary:
    total: int = 0
    new: int = 0
    refreshed: int = 0
    unstarred: int = 0
    login: str = ""

    def as_dict(self) -> dict[str, int | str]:
        return {
            "total": self.total,
            "new": self.new,
            "refreshed": self.refreshed,
            "unstarred": self.unstarred,
            "login": self.login,
        }


def enumerate_stars() -> list[StarredRepo]:
    """Return every starred repository for the authenticated user.

    Uses the star media type so each element carries ``starred_at``.
    """
    raw = gh_api_paginated("user/starred", accept=STAR_ACCEPT)
    return [StarredRepo.from_star_json(obj) for obj in raw]


def sync_stars(
    conn: sqlite3.Connection,
    *,
    stars: Iterable[StarredRepo] | None = None,
    login: str | None = None,
) -> SyncSummary:
    """Reconcile the portal store against the current star set.

    ``stars``/``login`` may be injected (for tests / offline runs); otherwise
    they are fetched live via gh.
    """
    storage.init_intake_schema(conn)
    star_list = list(stars) if stars is not None else enumerate_stars()
    who = login if login is not None else _safe_login()

    seen_at = storage.now_iso()
    summary = SyncSummary(total=len(star_list), login=who)

    live_node_ids: set[str] = set()
    for repo in star_list:
        if not repo.node_id:
            continue
        live_node_ids.add(repo.node_id)
        existing = storage.get_external_repo(conn, repo.node_id)
        was_unstarred = existing is not None and existing["currently_starred"] == 0
        is_new = storage.upsert_external_repo(conn, repo, seen_at=seen_at)
        if is_new:
            exchange_id = storage.seed_exchange(
                conn, node_id=repo.node_id, full_name=repo.full_name, state="STARRED",
            )
            storage.record_star_event(
                conn, node_id=repo.node_id, full_name=repo.full_name,
                event="star", at=repo.starred_at or seen_at, exchange_id=exchange_id,
            )
            summary.new += 1
        elif was_unstarred:
            # Re-star: record the event and reattach (or seed) an exchange spine.
            exchange = storage.exchange_for_repo(conn, repo.node_id)
            exchange_id = (
                exchange["exchange_id"] if exchange
                else storage.seed_exchange(
                    conn, node_id=repo.node_id, full_name=repo.full_name, state="STARRED",
                )
            )
            storage.record_star_event(
                conn, node_id=repo.node_id, full_name=repo.full_name,
                event="star", at=seen_at, exchange_id=exchange_id,
            )
            summary.refreshed += 1
        else:
            summary.refreshed += 1

    # Detect unstars: repos previously starred but absent from the live set.
    for row in storage.list_external_repos(conn, currently_starred=True):
        if row["node_id"] not in live_node_ids:
            storage.mark_unstarred(conn, row["node_id"])
            storage.record_star_event(
                conn, node_id=row["node_id"], full_name=row["full_name"],
                event="unstar", at=seen_at,
            )
            summary.unstarred += 1

    storage.set_meta(conn, "last_sync", seen_at)
    if who:
        storage.set_meta(conn, "gh_login", who)
    conn.commit()
    return summary


def _safe_login() -> str:
    try:
        return authenticated_login()
    except Exception:  # noqa: BLE001 - login is best-effort metadata
        return ""
