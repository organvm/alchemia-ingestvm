"""Epic 1 — star corpus sync: coverage, idempotency, star/unstar history."""

from __future__ import annotations

import pytest

from alchemia.github import storage
from alchemia.github.models import StarredRepo
from alchemia.github.sync import sync_stars


def _repo(node_id: str, full_name: str, **kw) -> StarredRepo:
    owner, name = full_name.split("/")
    return StarredRepo(
        full_name=full_name,
        node_id=node_id,
        owner=owner,
        name=name,
        url=f"https://github.com/{full_name}",
        starred_at=kw.get("starred_at", "2026-07-01T00:00:00Z"),
        primary_language=kw.get("primary_language", "Python"),
        license_spdx=kw.get("license_spdx", "MIT"),
    )


@pytest.fixture
def db(tmp_path):
    conn = storage.connect(tmp_path / "portal.db")
    yield conn
    conn.close()


def test_star_json_parsing_with_media_type():
    obj = {
        "starred_at": "2026-06-15T12:00:00Z",
        "repo": {
            "full_name": "astral-sh/ruff",
            "node_id": "R_kg123",
            "name": "ruff",
            "owner": {"login": "astral-sh", "type": "Organization"},
            "html_url": "https://github.com/astral-sh/ruff",
            "description": "An extremely fast Python linter",
            "topics": ["python", "linter"],
            "language": "Rust",
            "default_branch": "main",
            "license": {"spdx_id": "MIT"},
            "stargazers_count": 30000,
        },
    }
    repo = StarredRepo.from_star_json(obj)
    assert repo.full_name == "astral-sh/ruff"
    assert repo.starred_at == "2026-06-15T12:00:00Z"
    assert repo.owner_type == "Organization"
    assert repo.license_spdx == "MIT"
    assert repo.topics == ["python", "linter"]


def test_sync_inserts_and_seeds_exchange(db):
    stars = [_repo("R_1", "a/one"), _repo("R_2", "b/two")]
    summary = sync_stars(db, stars=stars, login="tester")
    assert summary.total == 2
    assert summary.new == 2
    assert summary.unstarred == 0
    counts = storage.counts(db)
    assert counts["external_repo"] == 2
    assert counts["currently_starred"] == 2
    # Every new star seeds an exchange spine row + a star event.
    assert counts["exchange"] == 2
    assert counts["star_event"] == 2


def test_sync_is_idempotent(db):
    stars = [_repo("R_1", "a/one"), _repo("R_2", "b/two")]
    sync_stars(db, stars=stars, login="tester")
    second = sync_stars(db, stars=stars, login="tester")
    assert second.new == 0
    assert second.refreshed == 2
    assert second.unstarred == 0
    counts = storage.counts(db)
    # Re-running creates no duplicate repos, exchanges, or events.
    assert counts["external_repo"] == 2
    assert counts["exchange"] == 2
    assert counts["star_event"] == 2


def test_sync_detects_unstar(db):
    sync_stars(db, stars=[_repo("R_1", "a/one"), _repo("R_2", "b/two")], login="t")
    # Second sync: R_2 no longer starred.
    summary = sync_stars(db, stars=[_repo("R_1", "a/one")], login="t")
    assert summary.unstarred == 1
    counts = storage.counts(db)
    assert counts["currently_starred"] == 1
    # Unstar is recorded as history, not deleted.
    assert counts["external_repo"] == 2
    events = db.execute(
        "SELECT event FROM star_event WHERE full_name='b/two' ORDER BY id",
    ).fetchall()
    assert [e["event"] for e in events] == ["star", "unstar"]


def test_restar_after_unstar(db):
    sync_stars(db, stars=[_repo("R_1", "a/one")], login="t")
    sync_stars(db, stars=[], login="t")  # unstar
    sync_stars(db, stars=[_repo("R_1", "a/one")], login="t")  # restar
    row = storage.get_external_repo(db, "R_1")
    assert row["currently_starred"] == 1
    counts = storage.counts(db)
    assert counts["currently_starred"] == 1
    # Full history retained: star -> unstar -> star.
    events = db.execute(
        "SELECT event FROM star_event WHERE node_id='R_1' ORDER BY id",
    ).fetchall()
    assert [e["event"] for e in events] == ["star", "unstar", "star"]
