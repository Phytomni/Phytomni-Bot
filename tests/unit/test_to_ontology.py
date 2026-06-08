# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the GeneNetwork TO ontology loader.

Pins the committed catalog's shape (process-cached list of typed
entries) and the prompt-injection formatter the resolver uses to
embed the catalog into LLM context.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.network.to_ontology import (
    DEPRECATED_UPSTREAM_STATUS,
    TO_ONTOLOGY_PATH,
    ToOntologyEntry,
    format_to_ontology_for_prompt,
    load_to_ontology,
)

pytestmark = pytest.mark.unit


def test_load_to_ontology_returns_non_empty_typed_list() -> None:
    """Committed catalog parses into a non-empty list of typed entries."""
    entries = load_to_ontology()
    assert len(entries) > 0
    assert all(isinstance(entry, ToOntologyEntry) for entry in entries)
    assert all(entry.id.startswith("TO:") for entry in entries)
    assert all(entry.name for entry in entries)


def test_load_to_ontology_returns_cached_singleton() -> None:
    """``lru_cache`` returns the same list object on repeated calls."""
    first = load_to_ontology()
    second = load_to_ontology()
    assert first is second


def test_load_to_ontology_includes_plant_height_anchor() -> None:
    """Catalog includes ``TO:0000207`` (plant height) — the test anchor.

    Pins the customer-supplied TSV allowlist's most common trait so a
    future regeneration that accidentally drops the entry surfaces
    here. The committed catalog also keeps the customer-curated name
    rather than the upstream .obo's ``shoot height`` variant.
    """
    entries = load_to_ontology()
    by_id = {entry.id: entry for entry in entries}
    anchor = by_id.get("TO:0000207")
    assert anchor is not None
    assert anchor.name == "plant height"


def test_format_to_ontology_for_prompt_is_one_line_per_entry() -> None:
    """Formatter emits one line per entry with ``id | name`` minimum."""
    entries = load_to_ontology()
    text = format_to_ontology_for_prompt(entries)
    lines = text.splitlines()
    assert len(lines) == len(entries)
    for line in lines:
        assert line.startswith("TO:")
        assert " | " in line


def test_format_to_ontology_for_prompt_skips_definition_field() -> None:
    """Prompt injection drops the verbose definition body.

    Definitions live in the JSON for human reading but stay out of
    LLM context so the resolver's per-call token cost stays bounded.
    """
    entries = load_to_ontology()
    text = format_to_ontology_for_prompt(entries)
    # Pick the first entry that carries a definition and assert its
    # definition text does not appear in the formatted output.
    with_def = next((e for e in entries if e.definition), None)
    assert with_def is not None
    assert with_def.definition not in text


def test_to_ontology_path_resolves_to_committed_file() -> None:
    """``TO_ONTOLOGY_PATH`` points at the committed config asset."""
    assert TO_ONTOLOGY_PATH.exists()
    assert TO_ONTOLOGY_PATH.name == "to_ontology.json"


def test_load_to_ontology_carries_deprecated_upstream_status_on_32() -> None:
    """32 entries the upstream PTO release no longer vouches for are flagged.

    Pins the customer-supplied TSV / OBO cross-reference outcome:
    31 ids are upstream ``is_obsolete: true`` (no replaced_by hint)
    + 1 id (``TO:0000139`` "grains per panicle") is absent from the
    upstream catalog entirely. The shipped JSON stamps both with
    ``status: deprecated_upstream`` so the resolver can warn on pick.
    A regenerator that loses the flag (or grows the count) surfaces
    here.
    """
    entries = load_to_ontology()
    deprecated = [
        entry
        for entry in entries
        if entry.status == DEPRECATED_UPSTREAM_STATUS
    ]
    assert len(deprecated) == 32
    deprecated_ids = {entry.id for entry in deprecated}
    assert "TO:0000139" in deprecated_ids  # OBO-missing anchor
    assert "TO:0000001" in deprecated_ids  # OBO-obsoleted anchor


def test_active_entries_have_empty_status() -> None:
    """Canonical (non-deprecated) entries carry an empty ``status``.

    Pins the JSON convention: only the 32 problematic ids carry the
    sentinel; the other 541 stay clean so a future schema migration
    that defaults the field can rely on ``status == ""`` meaning
    "still vouched for by upstream".
    """
    entries = load_to_ontology()
    anchor = next(e for e in entries if e.id == "TO:0000207")
    assert anchor.status == ""


def test_format_to_ontology_for_prompt_skips_status_field() -> None:
    """Prompt injection does not leak the status sentinel to the LLM.

    The deprecated-upstream flag is for operator observability, not
    LLM steering; surfacing it to the model could bias the resolver
    away from customer-still-uses ids in unintended ways.
    """
    entries = load_to_ontology()
    text = format_to_ontology_for_prompt(entries)
    assert DEPRECATED_UPSTREAM_STATUS not in text
