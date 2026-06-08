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

import json

import pytest
from pydantic import ValidationError

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


def test_every_entry_carries_a_known_status_value() -> None:
    """Closed-set invariant: every entry's ``status`` is in the allowlist.

    Iterates all 573 entries (not just an anchor) so a regenerator
    that fat-fingers the sentinel string or invents a new bucket
    surfaces here at test time. Pairs with the ``Literal`` typing on
    ``ToOntologyEntry.status`` — if the typo escapes pydantic's
    closed-set validation (e.g., a manual JSON edit + an empty
    string default in a new pydantic version), this test catches it.
    """
    entries = load_to_ontology()
    known_statuses = {"", DEPRECATED_UPSTREAM_STATUS}
    bad = [
        (entry.id, entry.status)
        for entry in entries
        if entry.status not in known_statuses
    ]
    assert not bad, f"entries with unknown status: {bad[:5]}"
    deprecated_count = sum(
        1 for entry in entries if entry.status == DEPRECATED_UPSTREAM_STATUS
    )
    canonical_count = len(entries) - deprecated_count
    assert canonical_count == 541
    assert deprecated_count == 32


def test_meta_counts_match_entry_aggregates() -> None:
    """``_meta`` aggregate counts are cross-validated against the entries.

    The JSON's self-describing ``_meta.upstream_deprecated_count`` (31)
    and ``upstream_missing_count`` (1) MUST equal the count of entries
    flagged ``status: deprecated_upstream`` (32 total). A regenerator
    that updates one side without the other silently makes the JSON
    lie about itself; this test forces both sides to move together.
    """
    raw = json.loads(TO_ONTOLOGY_PATH.read_text(encoding="utf-8"))
    meta = raw["_meta"]
    declared_total = (
        meta["upstream_deprecated_count"] + meta["upstream_missing_count"]
    )
    entries = load_to_ontology()
    actual_total = sum(
        1 for entry in entries if entry.status == DEPRECATED_UPSTREAM_STATUS
    )
    assert declared_total == actual_total


def test_rejects_unknown_status_value_at_load() -> None:
    """Typo'd ``status`` value raises ``ValidationError`` at model load.

    Locks down the closed-set guarantee the resolver's deprecation
    warning depends on: if a regenerator wrote
    ``"status": "deprecated_upstrem"`` (missing 'a') the typo silently
    disables the warning forever. The ``Literal`` typing makes that
    a load-time crash instead.
    """
    with pytest.raises(ValidationError):
        ToOntologyEntry.model_validate(
            {"id": "TO:0001", "name": "x", "status": "deprecated_upstrem"}
        )


def test_entries_are_frozen_against_in_process_mutation() -> None:
    """``frozen=True`` prevents per-test mutation from poisoning the cache.

    ``load_to_ontology`` is an ``lru_cache`` singleton; without
    ``frozen=True`` a test that monkeypatches an entry's status would
    leak into every later test in the run and create order-dependent
    failures.
    """
    entry = load_to_ontology()[0]
    with pytest.raises(ValidationError):
        setattr(entry, "status", DEPRECATED_UPSTREAM_STATUS)


def test_format_to_ontology_for_prompt_skips_status_field() -> None:
    """Prompt injection does not leak the status sentinel to the LLM.

    The deprecated-upstream flag is for operator observability, not
    LLM steering; surfacing it to the model could bias the resolver
    away from customer-still-uses ids in unintended ways.
    """
    entries = load_to_ontology()
    text = format_to_ontology_for_prompt(entries)
    assert DEPRECATED_UPSTREAM_STATUS not in text
