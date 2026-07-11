"""Materialization-level orchestration for BIFRONS.

Absorb a starred repository only as deeply as its resonance warrants:

* S0 INDEX      — identity/metadata (produced by ``sync``)
* S1 DOSSIER    — README/license/manifests/contracts/health (``dossier``)
* S2 INSPECT    — + a metadata-only source tree + notable modules
* S3 CONTRIBUTE — ephemeral fork/worktree/test env — deferred to the engine's
                  contribution pipeline (never performed here)

This keeps thousands of stars computationally manageable and prevents untrusted
repositories from becoming permanent local dependencies. No repository is ever
cloned wholesale and no fetched code is ever executed.
"""

from __future__ import annotations

import sqlite3

from alchemia.github import storage
from alchemia.github.client import GitHubError, gh_api
from alchemia.github.dossier import build_dossier
from alchemia.github.models import MaterializationLevel, StarredRepo

# Cap on tree entries recorded at S2 (paths only — never content).
S2_TREE_CAP = 4000


def _row_to_repo(row: sqlite3.Row) -> StarredRepo:
    import json

    return StarredRepo(
        full_name=row["full_name"],
        node_id=row["node_id"],
        owner=row["owner"],
        name=row["name"],
        url=row["url"],
        starred_at=row["first_starred_at"],
        description=row["description"],
        topics=json.loads(row["topics"] or "[]"),
        primary_language=row["primary_language"],
        owner_type=row["owner_type"],
        default_branch=row["default_branch"],
        archived=bool(row["archived"]),
        fork=bool(row["fork"]),
        is_private=bool(row["is_private"]),
        license_spdx=row["license_spdx"],
    )


def materialize(
    conn: sqlite3.Connection,
    node_id: str,
    level: MaterializationLevel,
) -> dict:
    """Materialize one external repo up to ``level``. Returns a result summary."""
    row = storage.get_external_repo(conn, node_id)
    if row is None:
        return {"status": "unknown-repo", "node_id": node_id}
    repo = _row_to_repo(row)

    if repo.is_private:
        # Private-star metadata stays local and minimal; do not deep-materialize.
        return {"status": "private-skip", "repo": repo.full_name, "level": "S0"}

    if level == MaterializationLevel.INDEX:
        storage.set_materialization_level(conn, node_id, "S0")
        conn.commit()
        return {"status": "indexed", "repo": repo.full_name, "level": "S0"}

    if level == MaterializationLevel.CONTRIBUTE:
        # S3 is an engine-owned action (ephemeral fork/worktree/test env).
        return {
            "status": "deferred-to-engine",
            "repo": repo.full_name,
            "level": "S3",
            "reason": "S3 contribution workspace is created by organvm-engine contrib",
        }

    # S1 / S2 both build the dossier; S2 additionally records the source tree.
    dossier, artifacts = build_dossier(repo, conn=conn, level=level)
    result = {
        "status": "materialized",
        "repo": repo.full_name,
        "level": level.value,
        "snapshot_ref": dossier.snapshot_ref,
        "artifacts": len(artifacts),
    }
    if level == MaterializationLevel.INSPECT:
        tree = _fetch_tree(repo.owner, repo.name, dossier.snapshot_ref)
        result["tree_entries"] = len(tree)
        result["notable_modules"] = _notable_modules(tree)
    return result


def _fetch_tree(owner: str, name: str, ref: str) -> list[str]:
    """Fetch the repo tree (paths only) at ref. Metadata, never file content."""
    if not ref:
        return []
    try:
        obj = gh_api(f"repos/{owner}/{name}/git/trees/{ref}?recursive=1")
    except GitHubError:
        return []
    if not isinstance(obj, dict):
        return []
    entries = [e.get("path", "") for e in obj.get("tree", []) if e.get("type") == "blob"]
    return entries[:S2_TREE_CAP]


def _notable_modules(tree: list[str]) -> list[str]:
    """Heuristic: top-level source dirs that look like modules/packages."""
    tops: dict[str, int] = {}
    for path in tree:
        head = path.split("/", 1)[0]
        if head in {".github", "docs", "tests", "test", "examples", "vendor"}:
            continue
        tops[head] = tops.get(head, 0) + 1
    ranked = sorted(tops.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, _ in ranked[:12]]
