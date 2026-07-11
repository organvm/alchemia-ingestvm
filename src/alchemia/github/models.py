"""Domain models for BIFRONS star intake.

These are plain dataclasses with dict (de)serialization so they round-trip
cleanly into SQLite/JSON/YAML without pulling in a dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MaterializationLevel(str, Enum):
    """How deeply a starred repository has been absorbed.

    Metadata-first by design: we never clone every starred repository, and we
    escalate the level only for high-resonance / contribution candidates.
    """

    INDEX = "S0"      # id/name/url/owner/description/topics/timestamps
    DOSSIER = "S1"    # + README/license/manifests/contribution files/health
    INSPECT = "S2"    # + selected source tree / architecture / dependency graph
    CONTRIBUTE = "S3"  # + ephemeral fork/worktree/test env (engine-owned)

    @property
    def rank(self) -> int:
        return {"S0": 0, "S1": 1, "S2": 2, "S3": 3}[self.value]


@dataclass
class StarredRepo:
    """A single starred repository, as enumerated from the GitHub API.

    ``node_id`` is the stable GitHub global node id (``R_...``); it is the
    durable identity that survives renames, unlike ``full_name``.
    """

    full_name: str
    node_id: str
    owner: str
    name: str
    url: str
    starred_at: str = ""
    description: str = ""
    topics: list[str] = field(default_factory=list)
    primary_language: str = ""
    owner_type: str = ""
    default_branch: str = "main"
    archived: bool = False
    fork: bool = False
    is_private: bool = False
    pushed_at: str = ""
    stargazers_count: int = 0
    open_issues_count: int = 0
    license_spdx: str = ""

    @classmethod
    def from_star_json(cls, obj: dict[str, Any]) -> StarredRepo:
        """Build from a ``/user/starred`` element.

        With the star media type each element is ``{starred_at, repo}``; without
        it, each element is the repo object directly.
        """
        starred_at = ""
        repo = obj
        if "repo" in obj and isinstance(obj["repo"], dict):
            starred_at = obj.get("starred_at", "")
            repo = obj["repo"]
        owner = repo.get("owner") or {}
        lic = repo.get("license") or {}
        return cls(
            full_name=repo.get("full_name", ""),
            node_id=repo.get("node_id", ""),
            owner=owner.get("login", ""),
            name=repo.get("name", ""),
            url=repo.get("html_url", ""),
            starred_at=starred_at,
            description=repo.get("description") or "",
            topics=list(repo.get("topics") or []),
            primary_language=repo.get("language") or "",
            owner_type=owner.get("type", ""),
            default_branch=repo.get("default_branch", "main"),
            archived=bool(repo.get("archived", False)),
            fork=bool(repo.get("fork", False)),
            is_private=bool(repo.get("private", False)),
            pushed_at=repo.get("pushed_at") or "",
            stargazers_count=int(repo.get("stargazers_count") or 0),
            open_issues_count=int(repo.get("open_issues_count") or 0),
            license_spdx=(lic.get("spdx_id") or "") if lic else "",
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Artifact:
    """A single absorbed artifact (README, license, manifest, policy file...).

    Every artifact is pinned: ``source_url`` + ``ref`` + ``sha256`` make the
    absorption reproducible and tamper-evident.
    """

    kind: str
    name: str
    source_url: str
    ref: str
    fetched_at: str
    sha256: str
    bytes_len: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Dossier:
    """A versioned intelligence record for one external repository."""

    schema_version: str
    external_repo: str
    github_node_id: str
    level: str
    starred_at: str
    snapshot_ref: str
    snapshot_at: str
    identity: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    contracts: dict[str, Any] = field(default_factory=dict)
    architecture: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
