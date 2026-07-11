"""Epic 2 — dossier generation (metadata-first, pinned, hashed)."""

from __future__ import annotations

import base64

import pytest

from alchemia.github import dossier as dossier_mod
from alchemia.github import licensing as licensing_mod
from alchemia.github.client import GitHubError
from alchemia.github.models import StarredRepo

FAKE_SHA = "abc123def4567890abc123def4567890abc12345"
PRESENT = {"README.md", "pyproject.toml", "CONTRIBUTING.md"}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def fake_gh_api(path: str, *, accept=None, jq=None):
    """Route a small deterministic GitHub API surface for tests."""
    if path.startswith("repos/") and "/commits/" in path and jq == ".sha":
        return FAKE_SHA
    if path.endswith("/license") and jq == ".license.spdx_id":
        return "MIT"
    if path.endswith("/languages"):
        return {"Python": 800, "Shell": 200}
    if "/git/trees/" in path:
        return {"tree": [
            {"path": "src/pkg/__init__.py", "type": "blob"},
            {"path": "src/pkg/core.py", "type": "blob"},
            {"path": "tests/test_core.py", "type": "blob"},
            {"path": "README.md", "type": "blob"},
        ]}
    if "/contents/" in path:
        # strip ?ref=
        rel = path.split("/contents/", 1)[1].split("?", 1)[0]
        if jq == ".sha":
            if rel in PRESENT:
                return "sha-" + rel
            raise GitHubError("404")
        if jq == ".content":
            return _b64("Please add a Signed-off-by line (DCO).")
        # full object fetch
        if rel in PRESENT:
            return {
                "type": "file",
                "content": _b64(f"# contents of {rel}\n"),
                "html_url": f"https://github.com/x/y/blob/main/{rel}",
                "size": 20,
            }
        raise GitHubError("404")
    raise GitHubError(f"unexpected path {path}")


@pytest.fixture(autouse=True)
def _patch_gh(monkeypatch):
    monkeypatch.setattr(dossier_mod, "gh_api", fake_gh_api)
    monkeypatch.setattr(licensing_mod, "gh_api", fake_gh_api)


def _repo() -> StarredRepo:
    return StarredRepo(
        full_name="astral-sh/ruff",
        node_id="R_kg1",
        owner="astral-sh",
        name="ruff",
        url="https://github.com/astral-sh/ruff",
        starred_at="2026-06-01T00:00:00Z",
        primary_language="Rust",
        license_spdx="MIT",
        default_branch="main",
    )


def test_build_dossier_pins_ref_and_hashes_artifacts():
    dossier, artifacts = dossier_mod.build_dossier(_repo())
    assert dossier.snapshot_ref == FAKE_SHA
    assert dossier.schema_version == "1.0"
    assert dossier.level == "S1"
    # README + CONTRIBUTING fetched; every artifact carries a sha256.
    names = {a.name for a in artifacts}
    assert "README.md" in names
    assert all(a.sha256 for a in artifacts)
    # Provenance links artifact -> hash.
    assert dossier.provenance["hashes"]["README.md"]


def test_dossier_detects_license_and_contracts():
    dossier, _ = dossier_mod.build_dossier(_repo())
    assert dossier.contracts["license"]["spdx"] == "MIT"
    assert dossier.contracts["license"]["class"] == "permissive"
    assert dossier.contracts["decision"] == "code-adaptation-with-attribution"
    assert dossier.contracts["contributing"] == "CONTRIBUTING.md"
    assert dossier.contracts["cla_or_dco"] == "dco"


def test_dossier_architecture_manifests():
    dossier, _ = dossier_mod.build_dossier(_repo())
    assert "pyproject.toml" in dossier.architecture["manifests"]
    assert "python" in dossier.architecture["test_strategy"]
    assert dossier.identity["languages"]["Python"] == 0.8
