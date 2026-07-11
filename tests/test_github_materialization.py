"""Materialization levels — S0/S1/S2 persist; S3 defers; private stays local."""

from __future__ import annotations

import pytest

from alchemia.github import dossier as dossier_mod
from alchemia.github import licensing as licensing_mod
from alchemia.github import materialize as mat_mod
from alchemia.github import storage
from alchemia.github.materialize import materialize
from alchemia.github.models import MaterializationLevel, StarredRepo
from alchemia.github.sync import sync_stars
from tests.test_github_dossier import fake_gh_api


@pytest.fixture(autouse=True)
def _patch_gh(monkeypatch):
    monkeypatch.setattr(dossier_mod, "gh_api", fake_gh_api)
    monkeypatch.setattr(licensing_mod, "gh_api", fake_gh_api)
    monkeypatch.setattr(mat_mod, "gh_api", fake_gh_api)


@pytest.fixture
def db(tmp_path):
    conn = storage.connect(tmp_path / "portal.db")
    yield conn
    conn.close()


def _seed(db, *, private=False):
    repo = StarredRepo(
        full_name="astral-sh/ruff",
        node_id="R_kg1",
        owner="astral-sh",
        name="ruff",
        url="https://github.com/astral-sh/ruff",
        starred_at="2026-06-01T00:00:00Z",
        primary_language="Rust",
        license_spdx="MIT",
        default_branch="main",
        is_private=private,
    )
    sync_stars(db, stars=[repo], login="t")
    return repo


def test_materialize_dossier_persists_and_advances_level(db):
    _seed(db)
    result = materialize(db, "R_kg1", MaterializationLevel.DOSSIER)
    assert result["status"] == "materialized"
    assert result["level"] == "S1"
    row = storage.get_external_repo(db, "R_kg1")
    assert row["materialization_level"] == "S1"
    # Dossier + snapshot + artifacts landed in the store.
    counts = storage.counts(db)
    assert counts["dossier"] == 1
    assert counts["repo_snapshot"] == 1
    assert counts["artifact"] >= 1
    # The dossier is linked to the star's exchange spine.
    doss = storage.get_dossier(db, "R_kg1", "S1")
    ex = storage.exchange_for_repo(db, "R_kg1")
    assert doss["exchange_id"] == ex["exchange_id"]


def test_materialize_inspect_records_tree(db):
    _seed(db)
    result = materialize(db, "R_kg1", MaterializationLevel.INSPECT)
    assert result["level"] == "S2"
    assert result["tree_entries"] == 4
    assert "src" in result["notable_modules"]


def test_materialize_dossier_is_idempotent(db):
    _seed(db)
    materialize(db, "R_kg1", MaterializationLevel.DOSSIER)
    materialize(db, "R_kg1", MaterializationLevel.DOSSIER)
    # UNIQUE(node_id, level) keeps exactly one dossier row.
    assert storage.counts(db)["dossier"] == 1


def test_private_repo_is_not_deep_materialized(db):
    _seed(db, private=True)
    result = materialize(db, "R_kg1", MaterializationLevel.DOSSIER)
    assert result["status"] == "private-skip"
    assert storage.counts(db)["dossier"] == 0


def test_contribute_level_defers_to_engine(db):
    _seed(db)
    result = materialize(db, "R_kg1", MaterializationLevel.CONTRIBUTE)
    assert result["status"] == "deferred-to-engine"
    assert result["level"] == "S3"


def test_unknown_repo(db):
    storage.init_intake_schema(db)
    result = materialize(db, "R_nope", MaterializationLevel.DOSSIER)
    assert result["status"] == "unknown-repo"
