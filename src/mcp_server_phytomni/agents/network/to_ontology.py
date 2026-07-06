# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Plant Trait Ontology catalog loader for the GeneNetwork resolver.

Reads the customer-filtered TO catalog committed at
``src/mcp_server_phytomni/config/to_ontology.json`` (CC-BY 4.0
Planteome, see the file's ``_meta.license`` field) into a typed list.
The catalog is loaded once per process via ``lru_cache`` so the
resolver's prompt-injection cost is paid at most once.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEPRECATED_UPSTREAM_STATUS",
    "TO_ONTOLOGY_PATH",
    "ToOntologyEntry",
    "format_to_ontology_for_prompt",
    "load_to_ontology",
]

TO_ONTOLOGY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "to_ontology.json"
)


DEPRECATED_UPSTREAM_STATUS = "deprecated_upstream"
_StatusLiteral = Literal["", "deprecated_upstream"]


class ToOntologyEntry(BaseModel):
    """One TO ontology term the resolver may surface as a candidate.

    ``status`` is ``"deprecated_upstream"`` for the 32 customer ids the
    upstream PTO release marks ``is_obsolete: true`` (31) or omits
    entirely (1, TO:0000139). Empty for the canonical 541 entries.
    The closed-set ``Literal`` type rejects regenerator typos at load
    time so a stray value like ``"deprecated_upstrem"`` raises a
    ``ValidationError`` instead of silently disabling the resolver's
    deprecation warning. ``frozen=True`` keeps the cached singleton
    list safe from accidental in-process mutation across tests.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    synonyms: list[str] = []
    definition: str = ""
    status: _StatusLiteral = ""


@lru_cache(maxsize=1)
def load_to_ontology() -> list[ToOntologyEntry]:
    """Load the committed TO catalog into a typed list (process-cached).

    Returns the customer-filtered set rather than the full upstream
    .obo so the resolver only ever picks ids the deployment is
    actually configured for.
    """
    payload = json.loads(TO_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    raw_entries: list = []
    if isinstance(payload, dict):
        raw_entries = payload.get("entries") or []
    return [ToOntologyEntry.model_validate(item) for item in raw_entries]


def format_to_ontology_for_prompt(entries: list[ToOntologyEntry]) -> str:
    """Format the catalog as a compact text block for LLM injection.

    One line per entry: ``TO:NNNNNNN | <name> | synonyms: ...``. Skips
    the definition (kept in the JSON for documentation only) so the
    prompt stays within the per-call token budget the resolver pays.
    """
    lines: list[str] = []
    for entry in entries:
        line = f"{entry.id} | {entry.name}"
        if entry.synonyms:
            line += f" | synonyms: {', '.join(entry.synonyms)}"
        lines.append(line)
    return "\n".join(lines)
