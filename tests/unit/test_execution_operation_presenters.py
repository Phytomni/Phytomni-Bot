# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite-schema tests for public operation presenters."""

from __future__ import annotations

from typing import cast


def test_registry_resolves_known_and_unknown_presenters() -> None:
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        OPERATION_PRESENTER_REGISTRY,
    )

    known = OPERATION_PRESENTER_REGISTRY.resolve("knowledge.search")
    assert known.operation_key == "knowledge.search"
    assert known.fallback_label == "Search knowledge"
    assert known.allowed_detail_fields == {
        "repository_count": "integer",
        "result_count": "integer",
    }
    assert known.counter_units == ("repositories", "results")

    unknown = OPERATION_PRESENTER_REGISTRY.resolve("private.dynamic.operation")
    assert unknown.operation_key == "operation.unknown"
    assert unknown.fallback_label == "Internal operation"
    assert unknown.allowed_detail_fields == {}
    assert unknown.counter_units == ()
    assert unknown.target_kinds == ()


def test_registry_sanitizes_metadata_counters_and_targets() -> None:
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        OPERATION_PRESENTER_REGISTRY,
    )

    knowledge = OPERATION_PRESENTER_REGISTRY.present(
        "knowledge.search",
        detail={
            "repository_count": 3,
            "result_count": 8,
            "query": "private query",
            "source_passage": "private evidence",
        },
        progress={"completed": 2, "total": 3, "unit": "repositories"},
        target={"kind": "artifact", "id": "must-not-pass"},
    )
    assert knowledge.detail == {"repository_count": 3, "result_count": 8}
    assert knowledge.progress == {
        "completed": 2,
        "total": 3,
        "unit": "repositories",
    }
    assert knowledge.target is None

    artifact = OPERATION_PRESENTER_REGISTRY.present(
        "artifact.package",
        detail={"artifact_count": 2, "path": "/srv/private"},
        progress={"completed": 1, "unit": "artifacts"},
        target={"kind": "artifact", "id": "execution-log-1"},
    )
    assert artifact.detail == {"artifact_count": 2}
    assert artifact.progress is None
    assert artifact.target == {"kind": "artifact", "id": "execution-log-1"}


def test_registry_drops_invalid_and_unknown_operation_payloads() -> None:
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        OPERATION_PRESENTER_REGISTRY,
    )

    review = OPERATION_PRESENTER_REGISTRY.present(
        "review.retrieve_dimension",
        detail={"ordinal": True, "total": -1, "prompt": "private"},
        progress={"completed": 2, "total": 1, "unit": "dimensions"},
        target={"kind": "download", "id": "private-target"},
    )
    assert review.detail == {}
    assert review.progress is None
    assert review.target is None

    unknown = OPERATION_PRESENTER_REGISTRY.present(
        "private.dynamic.operation",
        detail={"token": "private", "result": {"secret": True}},
        progress={"completed": 1, "total": 1, "unit": "items"},
        target={"kind": "artifact", "id": "private-target"},
    )
    assert unknown.operation_key == "operation.unknown"
    assert unknown.detail == {}
    assert unknown.progress is None
    assert unknown.target is None


def test_capability_serializes_finite_presenter_schemas() -> None:
    from mcp_server_phytomni.runtime.execution_trace_detail import (
        serialize_operation_record_capability,
    )

    capability = serialize_operation_record_capability()
    presenter_rows = cast(list[dict[str, object]], capability["presenters"])
    presenters = {
        presenter["operation_key"]: presenter for presenter in presenter_rows
    }

    assert presenters["review.retrieve_dimension"] == {
        "operation_key": "review.retrieve_dimension",
        "label_key": "execution.operation.review.retrieveDimension",
        "fallback_label": "Retrieve evidence",
        "semantic_kind": "operation",
        "allowed_detail_fields": {"ordinal": "integer", "total": "integer"},
        "counter_units": ["dimensions"],
        "target_kinds": [],
    }
    assert capability["unknown_presenter"] == {
        "operation_key": "operation.unknown",
        "label_key": "execution.operation.generic",
        "fallback_label": "Internal operation",
        "semantic_kind": "operation",
        "allowed_detail_fields": {},
        "counter_units": [],
        "target_kinds": [],
    }
