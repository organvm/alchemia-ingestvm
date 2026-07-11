"""License firewall + contribution-contract discovery for BIFRONS.

The portal must distinguish what it may legally do with a starred repository
before any code adaptation. This module classifies the license and discovers
contribution contracts (CONTRIBUTING/SECURITY/CoC/CLA-DCO), producing the
inputs the engine's inbound transmutation step consults.

READ-ONLY: everything here reads metadata via gh; nothing is cloned or run.
"""

from __future__ import annotations

from alchemia.github.client import GitHubError, gh_api

# SPDX ids that permit code adaptation with attribution + provenance.
PERMISSIVE_SPDX = frozenset({
    "MIT", "MIT-0", "APACHE-2.0", "BSD-2-CLAUSE", "BSD-3-CLAUSE", "BSD-3-CLAUSE-CLEAR",
    "ISC", "0BSD", "UNLICENSE", "ZLIB", "BSL-1.0", "PYTHON-2.0", "POSTGRESQL",
    "CC0-1.0", "WTFPL", "NCSA",
})

# SPDX ids whose obligations require explicit acceptance before code reuse.
COPYLEFT_SPDX = frozenset({
    "GPL-2.0", "GPL-3.0", "GPL-2.0-ONLY", "GPL-2.0-OR-LATER", "GPL-3.0-ONLY",
    "GPL-3.0-OR-LATER", "AGPL-3.0", "AGPL-3.0-ONLY", "AGPL-3.0-OR-LATER",
    "LGPL-2.1", "LGPL-3.0", "LGPL-2.1-ONLY", "LGPL-3.0-ONLY", "MPL-2.0",
    "EPL-2.0", "CDDL-1.0", "OSL-3.0", "EUPL-1.2",
})

CONTRACT_FILES = {
    "contributing": [
        "CONTRIBUTING.md", "CONTRIBUTING", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md",
    ],
    "security": ["SECURITY.md", ".github/SECURITY.md"],
    "code_of_conduct": ["CODE_OF_CONDUCT.md", ".github/CODE_OF_CONDUCT.md"],
    "pr_template": [".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md"],
}

# Default behavior permitted per license class (the firewall).
LICENSE_DECISION = {
    "permissive": "code-adaptation-with-attribution",
    "copyleft": "idea-or-interface-only-unless-obligations-accepted",
    "none": "no-code-or-asset-copying",
    "unknown": "no-code-or-asset-copying",
}


def classify_license(spdx: str) -> str:
    """Return 'permissive' | 'copyleft' | 'none' | 'unknown' for an SPDX id."""
    if not spdx or spdx.upper() in {"NOASSERTION", "NONE"}:
        return "none" if spdx else "unknown"
    key = spdx.upper()
    if key in PERMISSIVE_SPDX:
        return "permissive"
    if key in COPYLEFT_SPDX:
        return "copyleft"
    return "unknown"


def license_decision(license_class: str) -> str:
    """Map a license class to its permitted default behavior."""
    return LICENSE_DECISION.get(license_class, LICENSE_DECISION["unknown"])


def _path_exists(owner: str, name: str, path: str) -> bool:
    try:
        result = gh_api(f"repos/{owner}/{name}/contents/{path}", jq=".sha")
    except GitHubError:
        return False
    return bool(result)


def discover_contracts(owner: str, name: str, *, spdx: str = "") -> dict:
    """Discover license class + contribution contracts for a repository.

    ``spdx`` may be supplied from the already-known star metadata to skip a
    call; otherwise the license endpoint is queried.
    """
    resolved_spdx = spdx
    if not resolved_spdx:
        try:
            resolved_spdx = gh_api(f"repos/{owner}/{name}/license", jq=".license.spdx_id") or ""
        except GitHubError:
            resolved_spdx = ""

    license_class = classify_license(resolved_spdx)
    contracts: dict = {
        "license": {"spdx": resolved_spdx, "class": license_class},
        "decision": license_decision(license_class),
        "cla_or_dco": "unknown",
    }
    for label, candidates in CONTRACT_FILES.items():
        found = next((c for c in candidates if _path_exists(owner, name, c)), "")
        contracts[label] = found

    # DCO is commonly signalled by a "Signed-off-by" requirement in CONTRIBUTING.
    if contracts.get("contributing"):
        contracts["cla_or_dco"] = _detect_dco_cla(owner, name, contracts["contributing"])
    return contracts


def _detect_dco_cla(owner: str, name: str, path: str) -> str:
    """Best-effort DCO/CLA detection from the CONTRIBUTING file text."""
    try:
        text = gh_api(f"repos/{owner}/{name}/contents/{path}", jq=".content") or ""
    except GitHubError:
        return "unknown"
    if not text:
        return "unknown"
    import base64

    try:
        decoded = base64.b64decode(text).decode("utf-8", "ignore").lower()
    except (ValueError, UnicodeDecodeError):
        return "unknown"
    if "signed-off-by" in decoded or "developer certificate of origin" in decoded:
        return "dco"
    if "contributor license agreement" in decoded or "cla" in decoded:
        return "cla"
    return "none"
