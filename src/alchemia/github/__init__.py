"""BIFRONS — the GitHub star-intake face of alchemia.

The inbound half of the BIFRONS star<->contribution portal. Enumerates the
owner's starred repositories, absorbs each into a provenance-backed dossier at
metadata-first materialization levels (S0-S3), and persists everything into the
shared portal store (``~/.organvm/bifrons/portal.db``) that the organvm-engine
network/portal/contrib subsystems read downstream.

Naming: BIFRONS (Janus, two-faced) is the star<->contribution portal and is
deliberately distinct from IANVA (the MCP doorway/aggregator).
"""

from alchemia.github.models import (
    Artifact,
    Dossier,
    MaterializationLevel,
    StarredRepo,
)

__all__ = [
    "Artifact",
    "Dossier",
    "MaterializationLevel",
    "StarredRepo",
]
