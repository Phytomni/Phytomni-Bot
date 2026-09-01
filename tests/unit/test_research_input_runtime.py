# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Research HTTP runtime construction and restart tests."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.agents import (
    test_research_input_coordinator as coordinator_fixtures,
)
from tests.support.outbound_fakes import InlineObsRuntime

from mcp_server_phytomni.agents.research import dispatch_runtime
from mcp_server_phytomni.agents.research import recovery as recovery_module
from mcp_server_phytomni.agents.research.dispatch_outbox import (
    persist_plan_and_outbox,
)
from mcp_server_phytomni.api import research_input as research_input_api
from mcp_server_phytomni.storage.research_objects import (
    DirectResearchObjectMetadataPort,
    ResearchObjectCandidate,
    ResearchObjectResolveRequest,
)

pytestmark = pytest.mark.unit

coordinator_store = getattr(coordinator_fixtures, "_runtime_store")
MetadataPortType = getattr(coordinator_fixtures, "_RuntimeMetadataPort")
ProviderType = getattr(coordinator_fixtures, "_RuntimeProvider")
analyst_factory = getattr(coordinator_fixtures, "_runtime_analyst")
prepared_factory = getattr(coordinator_fixtures, "_runtime_prepared")
plan_factory = getattr(coordinator_fixtures, "_runtime_plan")


def test_api_lifespan_runtime_constructs_and_registers_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP production entrypoint builds the real coordinator seam."""
    store = coordinator_store(tmp_path, "api-runtime")
    sensitive = object()
    analyst_instances: list[Any] = []
    registered: list[Any] = []

    def fake_analyst_agent(**kwargs: Any) -> object:
        """Capture the production Analyst constructor arguments."""
        analyst_instances.append(kwargs)
        return object()

    monkeypatch.setitem(
        getattr(research_input_api, "_RUNTIME_STATE"), "current", None
    )
    monkeypatch.setattr(research_input_api, "AnalystAgent", fake_analyst_agent)
    monkeypatch.setattr(
        research_input_api, "get_sensitive_config", lambda: sensitive
    )
    monkeypatch.setattr(
        research_input_api,
        "ResearchInputStore",
        lambda _path: store,
    )
    monkeypatch.setattr(
        research_input_api,
        "register_recovery_service",
        registered.append,
    )
    monkeypatch.setattr(
        recovery_module,
        "register_recovery_service",
        lambda _service: None,
    )
    monkeypatch.setattr(
        dispatch_runtime,
        "build_research_object_metadata_port",
        MetadataPortType,
    )

    coordinator = research_input_api.ensure_research_input_runtime(
        str(tmp_path / "api-runtime.db")
    )

    assert coordinator.outbox is not None
    assert coordinator.recovery is not None
    assert registered == [coordinator.recovery]
    assert len(analyst_instances) == 1
    captured = analyst_instances[0]
    assert captured["sensitive_config"] is sensitive
    assert isinstance(
        captured["analyst_config"],
        research_input_api.InSilicoResearchConfig,
    )
    assert captured["analyst_config"].COMPUTE_RESOURCE == "small"


@pytest.mark.asyncio
async def test_direct_runtime_re_resolves_only_after_authority_restart(
    tmp_path: Path,
) -> None:
    """A direct-port restart re-resolves exact metadata before submission."""
    store = coordinator_store(tmp_path, "direct-runtime")
    head_calls: list[tuple[str, str]] = []

    def get_object_metadata(**kwargs: str) -> SimpleNamespace:
        """Return stable HEAD metadata for the exact child object."""
        head_calls.append((kwargs["bucketName"], kwargs["objectKey"]))
        return SimpleNamespace(
            status=200,
            body=SimpleNamespace(
                contentLength=17,
                etag="etag-001",
                versionId="version-001",
                lastModified="2026-08-08T00:00:00+00:00",
            ),
        )

    client = SimpleNamespace(getObjectMetadata=get_object_metadata)
    initial_port = DirectResearchObjectMetadataPort(
        "dev-bucket", InlineObsRuntime(lambda: client)
    )
    fingerprint = hashlib.sha256(str(tmp_path).encode()).hexdigest()
    candidate = ResearchObjectCandidate(
        "dataset-001", "obs://dev-bucket/data.tsv", ".tsv"
    )
    authority = (
        await initial_port.resolve(
            ResearchObjectResolveRequest(
                "run-runtime", fingerprint, (candidate,)
            )
        )
    )[0]
    prepared = prepared_factory()
    prepared = replace(
        prepared,
        authority_ids=(authority.authority_id,),
        authorities=(replace(prepared.authorities[0], authority=authority),),
    )
    submitted: list[Any] = []
    restarted_port = DirectResearchObjectMetadataPort(
        "dev-bucket", InlineObsRuntime(lambda: client)
    )
    runtime = dispatch_runtime.build_research_dispatch_runtime(
        store,
        ProviderType(),
        analyst_agent=analyst_factory(submitted),
        analyst_config=type(
            "Config",
            (),
            {"USER_ID": "owner", "COMPUTE_RESOURCE": "medium"},
        )(),
        sensitive_config=object(),
        metadata_port=restarted_port,
        lease_owner="direct-worker",
    )
    record = persist_plan_and_outbox(
        store,
        "run-runtime",
        0,
        prepared,
        plan_factory(fingerprint),
    )[0]

    disposition = await runtime.outbox.dispatch_once(
        record.dispatch_id, "direct-worker"
    )

    assert disposition.state == "accepted"
    assert head_calls == [("dev-bucket", "data.tsv")] * 2
    assert (
        submitted[0][0]["research_grant_sidecar"]["objects"][0]["grant_id"]
        != authority.authority_id
    )
