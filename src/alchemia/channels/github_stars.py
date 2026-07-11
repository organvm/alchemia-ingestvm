"""GitHub stars capture channel — the inbound source for BIFRONS.

Follows the same shape as the other alchemia channels (bookmarks, apple_notes,
...): a plain function returning ``list[dict]``. Here each dict is a starred
repository. Unlike the aesthetic channels, stars are absorbed into the portal
store (dossiers + resonance) rather than only appended to ``taste.yaml``; the
optional ``as_references`` helper still records a shallow aesthetic signal for
repos whose interest is primarily stylistic.
"""

from __future__ import annotations

from alchemia.github.sync import enumerate_stars


def sync_github_stars() -> list[dict]:
    """Enumerate the authenticated user's starred repositories as dicts."""
    return [repo.to_dict() for repo in enumerate_stars()]


def as_references(stars: list[dict]) -> list[dict]:
    """Project stars into shallow aesthetic references (S0 signal only).

    Repo *intelligence* belongs in dossiers, not taste.yaml — this only
    surfaces the star as a narrative/aesthetic breadcrumb.
    """
    refs = []
    for star in stars:
        refs.append(
            {
                "type": "url",
                "source": star.get("url", ""),
                "tags": ["github-star", star.get("primary_language", "").lower() or "repo"],
                "notes": star.get("description", ""),
            },
        )
    return refs
