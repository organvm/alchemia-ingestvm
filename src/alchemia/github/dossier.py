"""Dossier construction for BIFRONS — the S1 absorption product.

Builds a versioned, provenance-backed intelligence record for one external
repository from metadata only. Every dossier is pinned to an exact commit sha
so the absorption is reproducible; every fetched artifact carries a sha256.

We never clone the repository and never execute any of its code.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from alchemia.github import storage
from alchemia.github.client import GitHubError, gh_api
from alchemia.github.licensing import discover_contracts
from alchemia.github.models import Artifact, Dossier, MaterializationLevel, StarredRepo

DOSSIER_SCHEMA_VERSION = "1.0"

# Manifests we probe for (presence only) to sketch the architecture cheaply.
MANIFEST_FILES = [
    "pyproject.toml", "setup.py", "requirements.txt", "package.json", "go.mod",
    "Cargo.toml", "pom.xml", "build.gradle", "Gemfile", "composer.json",
    "Dockerfile", "flake.nix", ".github/workflows",
]

README_CANDIDATES = ["README.md", "README.rst", "README", "readme.md"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_ref(owner: str, name: str, branch: str) -> str:
    """Pin the snapshot to the default branch's HEAD sha."""
    try:
        sha = gh_api(f"repos/{owner}/{name}/commits/{branch}", jq=".sha")
        return sha or ""
    except GitHubError:
        return ""


def _fetch_file(owner: str, name: str, path: str, ref: str) -> tuple[Artifact, bytes] | None:
    """Fetch one file's content at ``ref``; return (Artifact, raw_bytes) or None."""
    api_path = f"repos/{owner}/{name}/contents/{path}"
    if ref:
        api_path += f"?ref={ref}"
    try:
        obj = gh_api(api_path)
    except GitHubError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "file":
        return None
    encoded = obj.get("content") or ""
    try:
        raw = base64.b64decode(encoded)
    except (ValueError, TypeError):
        raw = b""
    art = Artifact(
        kind=_artifact_kind(path),
        name=path,
        source_url=obj.get("html_url", ""),
        ref=ref,
        fetched_at=storage.now_iso(),
        sha256=_sha256(raw),
        bytes_len=len(raw),
        truncated=bool(obj.get("size", 0) and not encoded),
    )
    return art, raw


def _artifact_kind(path: str) -> str:
    lower = path.lower()
    if lower.startswith("readme"):
        return "readme"
    if lower.startswith("license"):
        return "license"
    if "contributing" in lower:
        return "contributing"
    if "security" in lower:
        return "security"
    return "manifest"


def _probe_manifests(owner: str, name: str, ref: str) -> list[str]:
    present: list[str] = []
    for path in MANIFEST_FILES:
        api_path = f"repos/{owner}/{name}/contents/{path}"
        if ref:
            api_path += f"?ref={ref}"
        try:
            sha = gh_api(api_path, jq=".sha")
        except GitHubError:
            sha = ""
        if sha:
            present.append(path)
    return present


def _languages(owner: str, name: str) -> dict[str, float]:
    try:
        raw = gh_api(f"repos/{owner}/{name}/languages")
    except GitHubError:
        return {}
    if not isinstance(raw, dict) or not raw:
        return {}
    total = sum(raw.values()) or 1
    return {lang: round(count / total, 3) for lang, count in raw.items()}


def build_dossier(
    repo: StarredRepo,
    *,
    conn: Any = None,
    level: MaterializationLevel = MaterializationLevel.DOSSIER,
) -> tuple[Dossier, list[Artifact]]:
    """Build (and optionally persist) an S1 dossier for a starred repository.

    If ``conn`` is provided, the dossier + artifacts are written to the portal
    store and the repo's materialization level is advanced.
    """
    owner, name = repo.owner, repo.name
    ref = _resolve_ref(owner, name, repo.default_branch)

    artifacts: list[Artifact] = []
    # README
    for candidate in README_CANDIDATES:
        fetched = _fetch_file(owner, name, candidate, ref)
        if fetched:
            artifacts.append(fetched[0])
            break
    # Contracts (license class + policy files)
    contracts = discover_contracts(owner, name, spdx=repo.license_spdx)
    for label in ("contributing", "security", "code_of_conduct"):
        path = contracts.get(label)
        if path:
            fetched = _fetch_file(owner, name, path, ref)
            if fetched:
                artifacts.append(fetched[0])

    manifests = _probe_manifests(owner, name, ref)
    languages = _languages(owner, name) or (
        {repo.primary_language: 1.0} if repo.primary_language else {}
    )

    dossier = Dossier(
        schema_version=DOSSIER_SCHEMA_VERSION,
        external_repo=repo.full_name,
        github_node_id=repo.node_id,
        level=level.value,
        starred_at=repo.starred_at,
        snapshot_ref=ref,
        snapshot_at=storage.now_iso(),
        identity={
            "owner_type": repo.owner_type,
            "description": repo.description,
            "topics": repo.topics,
            "primary_language": repo.primary_language,
            "languages": languages,
        },
        state={
            "archived": repo.archived,
            "fork": repo.fork,
            "default_branch": repo.default_branch,
            "last_push_at": repo.pushed_at,
            "stargazers_count": repo.stargazers_count,
            "open_issue_count": repo.open_issues_count,
        },
        contracts=contracts,
        architecture={
            "manifests": manifests,
            "test_strategy": _infer_test_strategy(manifests),
            "deployment_model": "container" if "Dockerfile" in manifests else "",
        },
        provenance={
            "captured_by": "alchemia.github-stars",
            "source_url": repo.url,
            "snapshot_ref": ref,
            "artifacts": [a.name for a in artifacts],
            "hashes": {a.name: a.sha256 for a in artifacts},
        },
    )

    if conn is not None:
        exchange = storage.exchange_for_repo(conn, repo.node_id)
        exchange_id = exchange["exchange_id"] if exchange else ""
        storage.insert_snapshot(
            conn, node_id=repo.node_id, full_name=repo.full_name, ref=ref,
            pushed_at=repo.pushed_at, stargazers_count=repo.stargazers_count,
            open_issue_count=repo.open_issues_count,
        )
        for art in artifacts:
            storage.insert_artifact(conn, repo.node_id, repo.full_name, art)
        storage.upsert_dossier(conn, dossier, exchange_id=exchange_id)
        storage.set_materialization_level(conn, repo.node_id, level.value)
        conn.commit()

    return dossier, artifacts


def _infer_test_strategy(manifests: list[str]) -> list[str]:
    strategy: list[str] = []
    if "pyproject.toml" in manifests or "setup.py" in manifests:
        strategy.append("python")
    if "package.json" in manifests:
        strategy.append("node")
    if "go.mod" in manifests:
        strategy.append("go")
    if "Cargo.toml" in manifests:
        strategy.append("rust")
    if ".github/workflows" in manifests:
        strategy.append("github-actions")
    return strategy
