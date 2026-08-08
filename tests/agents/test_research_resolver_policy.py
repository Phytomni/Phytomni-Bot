# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for the bounded, lossless Research resolver work planner."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mcp_server_phytomni.agents.research.document_evidence import (
    ExtractedResearchEvidence,
    ResearchEvidenceUnit,
)
from mcp_server_phytomni.agents.research.input_contracts import SourceSpan
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputInventory,
)
from mcp_server_phytomni.agents.research.resolver_policy import (
    ResearchResolverPolicy,
    ResearchResolverPolicyError,
    canonical_json_bytes,
    plan_resolver_work,
    subdivide_verified_context_rejection,
)

pytestmark = pytest.mark.agent


class _ExactEstimator:
    """Record deterministic requests presented to the injected estimator."""

    def __init__(self) -> None:
        self.requests: list[bytes] = []

    def estimate(self, serialized_request: bytes) -> int:
        self.requests.append(serialized_request)
        return len(serialized_request) // 4 + 1


def _policy(**changes: object) -> ResearchResolverPolicy:
    """Build a small but usable policy without provider/tokenizer access."""
    values: dict[str, object] = {
        "schema_version": 1,
        "model_id": "phyto-research",
        "context_token_limit": 256,
        "output_token_reserve": 16,
        "prompt_token_overhead": 8,
        "schema_token_overhead": 8,
        "safety_margin_tokens": 8,
        "max_serialized_request_bytes": 512,
        "max_description_chars": 200,
        "overlap_chars": 12,
        "provider_identity": "test-provider",
        "provider_idempotency_supported": True,
        "provider_status_query_supported": True,
    }
    values.update(changes)
    return ResearchResolverPolicy(**values)  # type: ignore[arg-type]


def _unit(
    evidence_id: str,
    text: str,
    *,
    kind: str = "document_section",
    ordinal: int = 0,
    span: SourceSpan | None = None,
) -> ResearchEvidenceUnit:
    """Build one opaque evidence unit with deliberate source metadata."""
    return ResearchEvidenceUnit(
        evidence_id=evidence_id,
        source_kind=kind,  # type: ignore[arg-type]
        source_ordinal=ordinal,
        source_span=span,
        content_digest=f"digest-{evidence_id}",
        text=text,
        dataset_ids=("dataset_001",),
    )


def _evidence(*units: ResearchEvidenceUnit) -> ExtractedResearchEvidence:
    """Return immutable evidence without using document extraction I/O."""
    return ExtractedResearchEvidence(
        units=units, document_digests=(), coverage_digest="coverage"
    )


def _inventory() -> ResearchInputInventory:
    """Return the opaque inventory shape required by the planner."""
    return ResearchInputInventory((), (), (), "inventory-digest")


def _covered_text(plan) -> str:
    """Recover ordered fragment text from the exact canonical requests."""
    return "".join(
        fragment["text"][fragment["overlap_chars"] :]  # noqa: E203
        for observation in plan.observation_units
        for fragment in json.loads(observation.serialized_request)["evidence"]
    )


def test_policy_fingerprint_changes_for_every_semantic_field() -> None:
    """Every versioned policy field participates in a canonical fingerprint."""
    policy = _policy()
    for field in policy.__dataclass_fields__:
        value = getattr(policy, field)
        changed = (
            not value
            if isinstance(value, bool)
            else value + 1 if isinstance(value, int) else value + "-changed"
        )
        assert (
            replace(policy, **{field: changed}).fingerprint()
            != policy.fingerprint()
        )


def test_planner_uses_canonical_bytes_and_budgets() -> None:
    """The injected estimator sees only canonical serialized requests."""
    estimator = _ExactEstimator()
    evidence = _evidence(_unit("query_span_001", "水稻 expression"))

    plan = plan_resolver_work(evidence, _inventory(), _policy(), estimator)

    assert plan.required_evidence_ids == ("query_span_001",)
    assert estimator.requests == [plan.observation_units[0].serialized_request]
    assert plan.observation_units[0].serialized_bytes == len(
        plan.observation_units[0].serialized_request
    )
    assert plan.observation_units[
        0
    ].serialized_request == canonical_json_bytes(
        json.loads(plan.observation_units[0].serialized_request)
    )
    assert plan.observation_units[0].estimated_tokens == (
        len(plan.observation_units[0].serialized_request) // 4 + 1
    )


def test_tiny_context_covers_all_units_and_preserves_oversized_suffix() -> (
    None
):
    """Multibyte evidence is partitioned losslessly under both hard caps."""
    original = "甲乙丙丁" * 100
    evidence = _evidence(
        _unit(
            "query_span_001",
            original,
            kind="query",
            span=SourceSpan(0, 400, "query"),
        ),
        _unit("document_001_page_001", "page text", kind="pdf_page"),
        _unit("document_002_section_001", "section text"),
    )
    policy = _policy(
        context_token_limit=128,
        output_token_reserve=8,
        prompt_token_overhead=8,
        schema_token_overhead=8,
        safety_margin_tokens=8,
        max_serialized_request_bytes=512,
        overlap_chars=4,
    )

    plan = plan_resolver_work(
        evidence, _inventory(), policy, _ExactEstimator()
    )

    assert {
        evidence_id
        for unit in plan.observation_units
        for evidence_id in unit.evidence_ids
    } == set(plan.required_evidence_ids)
    assert all(
        unit.serialized_bytes <= policy.max_serialized_request_bytes
        for unit in plan.observation_units
    )
    assert (
        _covered_text(plan).replace("甲乙丙丁", "") == "page textsection text"
    )
    assert original in _covered_text(plan)


def test_boundaries_and_overlap_do_not_create_extra_required_coverage() -> (
    None
):
    """Page, section, paragraph, and source spans keep bounded membership."""
    evidence = _evidence(
        _unit(
            "document_001_page_001", "first page", kind="pdf_page", ordinal=0
        ),
        _unit(
            "document_001_page_002", "second page", kind="pdf_page", ordinal=1
        ),
        _unit("document_002_section_001", "one\n\ntwo\n\nthree", ordinal=0),
        _unit(
            "query_span_001",
            "question",
            kind="query",
            span=SourceSpan(9, 17, "query"),
        ),
    )
    plan = plan_resolver_work(
        evidence,
        _inventory(),
        _policy(max_serialized_request_bytes=512),
        _ExactEstimator(),
    )

    assert plan.required_evidence_ids == tuple(
        unit.evidence_id for unit in evidence.units
    )
    assert all(unit.evidence_ids for unit in plan.observation_units)
    assert plan.digest
    assert (
        plan.policy_fingerprint
        == _policy(max_serialized_request_bytes=512).fingerprint()
    )


def test_verified_context_rejection_subdivides_without_evidence_loss() -> None:
    """Only a verified limit produces deterministic smaller children."""
    evidence = _evidence(
        _unit("document_001_section_001", "alpha beta gamma delta " * 12)
    )
    policy = _policy(max_serialized_request_bytes=400)
    plan = plan_resolver_work(
        evidence, _inventory(), policy, _ExactEstimator()
    )
    parent = plan.observation_units[0]

    refined = subdivide_verified_context_rejection(
        plan, parent.unit_id, policy, _ExactEstimator(), verified=True
    )

    children = [
        unit
        for unit in refined.observation_units
        if unit.unit_id.startswith(parent.unit_id + ".")
    ]
    assert len(children) == 2
    assert all(
        unit.serialized_bytes < parent.serialized_bytes for unit in children
    )
    assert {item for unit in children for item in unit.evidence_ids} == set(
        parent.evidence_ids
    )
    with pytest.raises(ResearchResolverPolicyError):
        subdivide_verified_context_rejection(
            plan, parent.unit_id, policy, _ExactEstimator(), verified=False
        )
