"""Authenticated GitHub adapter for BIFRONS star intake.

Thin wrapper over the ``gh`` CLI so the portal never has to manage tokens
itself: ``gh`` already holds the authenticated session. This is the single
integration seam the design calls for ("a dedicated authenticated user-stars
adapter as its first integration seam").

All calls are READ-ONLY. Nothing here mutates a repository, and nothing here
executes code fetched from a starred repository — only metadata/JSON is read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

# The star media type is required to obtain ``starred_at`` on each star; the
# plain ``/user/starred`` response omits it.
STAR_ACCEPT = "application/vnd.github.star+json"


class GitHubError(RuntimeError):
    """Raised when the gh CLI is missing, unauthenticated, or errors."""


def gh_available() -> bool:
    """True when the gh CLI is installed and reports an authenticated user."""
    if shutil.which("gh") is None:
        return False
    try:
        run_gh(["api", "user", "-q", ".login"])
    except GitHubError:
        return False
    return True


def run_gh(args: list[str], *, check: bool = True) -> str:
    """Run ``gh`` with the given args and return stdout.

    Raises GitHubError on a non-zero exit (unless ``check`` is False, in which
    case stdout is returned as-is).
    """
    if shutil.which("gh") is None:
        raise GitHubError("gh CLI not found on PATH")
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - environment failure
        raise GitHubError(f"failed to invoke gh: {exc}") from exc
    if check and result.returncode != 0:
        raise GitHubError(
            f"gh {' '.join(args)} exited {result.returncode}: {result.stderr.strip()}",
        )
    return result.stdout


def gh_api(path: str, *, accept: str | None = None, jq: str | None = None) -> Any:
    """GET a single GitHub API path and parse the JSON response.

    Returns the parsed JSON (dict/list), or the raw string if ``jq`` is set.
    """
    args = ["api", path]
    if accept:
        args += ["-H", f"Accept: {accept}"]
    if jq:
        args += ["-q", jq]
        return run_gh(args).strip()
    out = run_gh(args)
    return json.loads(out) if out.strip() else None


def gh_api_paginated(path: str, *, accept: str | None = None) -> list[Any]:
    """GET a paginated GitHub API path, concatenating every page.

    ``gh api --paginate --slurp`` emits a single JSON array of all pages when
    the endpoint returns arrays. We fall back to per-page concatenation if the
    installed gh predates ``--slurp``.
    """
    args = ["api", "--paginate", "--slurp", path]
    if accept:
        args += ["-H", f"Accept: {accept}"]
    try:
        out = run_gh(args)
        data = json.loads(out) if out.strip() else []
    except GitHubError:
        # Older gh without --slurp: paginate returns concatenated arrays which
        # are not valid combined JSON. Read page-by-page instead.
        return _paginate_manual(path, accept=accept)
    # --slurp yields a list of pages (each a list) OR a flat list; normalize.
    flat: list[Any] = []
    for item in data:
        if isinstance(item, list):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def _paginate_manual(path: str, *, accept: str | None = None) -> list[Any]:
    """Manual pagination fallback via the ``page`` query parameter."""
    results: list[Any] = []
    page = 1
    joiner = "&" if "?" in path else "?"
    while True:
        page_path = f"{path}{joiner}per_page=100&page={page}"
        chunk = gh_api(page_path, accept=accept)
        if not chunk:
            break
        results.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return results


def authenticated_login() -> str:
    """Return the authenticated gh user's login (raises if unauthenticated)."""
    return gh_api("user", jq=".login")
