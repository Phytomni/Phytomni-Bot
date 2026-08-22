# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared deterministic Research-domain test builders."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from mcp_server_phytomni.agents.research.document_evidence import (
    ManagedDocumentPayload,
)
from mcp_server_phytomni.agents.research.input_inventory import (
    ResearchInputSnapshot,
    ResearchInventoryEntry,
)
from mcp_server_phytomni.agents.research.resolver_policy import (
    ResearchResolverPolicy,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore

RESEARCH_CONTRACT_FORBIDDEN_MARKERS = (
    "bearer ",
    "obs://",
    "http://",
    "https://",
    "/home/",
)


def staged_document_payload(body: bytes) -> ManagedDocumentPayload:
    """Write one fixture body to a cleanup-owned local file."""
    handle, path = tempfile.mkstemp(prefix="research-evidence-")
    try:
        os.write(handle, body)
    finally:
        os.close(handle)
    return ManagedDocumentPayload(
        path=path, size_bytes=len(body), cleanup=True
    )


def resolver_policy(**changes: object) -> ResearchResolverPolicy:
    """Build a small deterministic policy without provider/tokenizer access."""
    policy = ResearchResolverPolicy(
        schema_version=1,
        model_id="phyto-research",
        context_token_limit=256,
        output_token_reserve=16,
        prompt_token_overhead=8,
        schema_token_overhead=8,
        safety_margin_tokens=8,
        max_serialized_request_bytes=512,
        max_description_chars=200,
        overlap_chars=8,
        provider_identity="test-provider",
        provider_idempotency_supported=False,
        provider_status_query_supported=False,
    )
    return cast(Any, replace)(policy, **changes)


def research_inventory_entry(**values: Any) -> ResearchInventoryEntry:
    """Build a trusted Research inventory entry for focused test fixtures."""
    dataset_id = values["dataset_id"]
    snapshot = ResearchInputSnapshot(
        lane=values["lane"],
        size_bytes=values["size_bytes"],
        state_version=values.get("state_version"),
        completed_at=values.get("completed_at"),
        etag=values.get("etag"),
        version_id=values.get("version_id"),
        last_modified=values.get("last_modified"),
        placeholder=values.get("placeholder", False),
        purpose=values["purpose"],
        snapshot_digest=f"snapshot-{dataset_id}",
    )
    return ResearchInventoryEntry(
        dataset_id=dataset_id,
        lane=values["lane"],
        lane_ordinal=values["lane_ordinal"],
        exact_reference=values["exact_reference"],
        comparison_digest=values["comparison_digest"],
        safe_basename=values["safe_basename"],
        compound_suffix=values["compound_suffix"],
        size_bytes=values["size_bytes"],
        media_hint=values["media_hint"],
        purpose=values["purpose"],
        user_hint=values.get("user_hint"),
        source_span=values.get("source_span"),
        snapshot=snapshot,
        authority_id=values.get("authority_id"),
    )


def install_local_server_layer_marker(
    original_marker: Any, module_file: str
) -> Any:
    """Return a layer marker that keeps one fake-boundary module offline."""
    module_path = Path(module_file).resolve()

    def marker(item: Any) -> str | None:
        if Path(item.path).resolve() == module_path:
            return "server"
        return original_marker(item)

    return marker


def local_server_pytest_generate_tests(
    original_marker: Any, module_file: str, test_config: Any
) -> Callable[[Any], None]:
    """Build a pytest hook that keeps one fake-boundary module offline."""

    def pytest_generate_tests(metafunc: Any) -> None:
        del metafunc
        setattr(
            test_config,
            "_layer_marker_for_item",
            install_local_server_layer_marker(original_marker, module_file),
        )

    return pytest_generate_tests


def persist_research_resolution(
    store: ResearchInputStore, run_id: str, *, query_length: int
) -> None:
    """Persist the smallest valid Research resolution for fixture setup."""
    assert store.persist_resolution(
        run_id,
        original_query_digest="q" * 64,
        original_query_length=query_length,
        effective_query="query",
        source_map={},
        parsed_candidates=[],
        managed_snapshot=[],
        evidence_digest="e" * 64,
        work_digest="w" * 64,
    )


def research_callbacks_through(stage: str) -> list[str]:
    """Return the deterministic Research coordinator callback prefix."""
    stages = ["metadata", "extract"] + [
        "resolve",
        "revalidate",
        "validate_native",
    ]
    return stages[: stages.index(stage) + 1]


def research_relay_snapshot_payload(
    dataset_id: str, *, snapshot_digest: str | None = None
) -> dict[str, object]:
    """Return the canonical safe relay snapshot fixture projection."""
    return {
        "dataset_id": dataset_id,
        "size_bytes": 17,
        "etag": "etag-17",
        "version_id": "version-1",
        "last_modified": "2026-08-08T00:00:00Z",
        "placeholder": False,
        "snapshot_digest": snapshot_digest or f"digest-{dataset_id}",
    }
