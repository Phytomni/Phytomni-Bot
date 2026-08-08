# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP Research input admission tests.

These tests keep the HTTP-only coordinator boundary separate from the MCP
schema.  The route adapter must perform an identity-only replay lookup before
parsing or resolving any user input.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.agents.research.input_inventory import (
    ManagedResearchAssetSnapshot,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.research_input import (
    ResearchHttpAdmissionInput,
    ResearchRoutePreflight,
)
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.server


def _store(tmp_path: Path) -> Any:
    """Create the public tables before the Research private schema."""
    database = str(tmp_path / "research-http.sqlite")
    RunRegistry(database).create_run(
        RunSpec(
            run_id="unrelated",
            user_id="owner-1",
            agent="research",
            origin="api",
        )
    )
    return ResearchInputStore(database)


def _request(
    *,
    query: str = "summarize the inputs",
    key: str | None = "http-key",
) -> ResearchHttpAdmissionInput:
    """Build one direct native HTTP request."""
    return ResearchHttpAdmissionInput(
        owner="owner-1",
        idempotency_key=key,
        conversation=None,
        original_query=query,
        managed_asset_ids=("asset-1",),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
    )


async def test_replay_is_checked_before_parser_or_managed_resolution(
    tmp_path: Path,
) -> None:
    """Exact replays do not repeat parser, resolver, or worker side effects."""
    store = _store(tmp_path)
    calls = SimpleNamespace(parse=0, resolve=0, launch=0)

    def parse(query: str, bucket: str) -> Any:
        calls.parse += 1
        return parse_research_input(query, bucket)

    def resolve(asset_ids: tuple[str, ...]) -> tuple[Any, ...]:
        calls.resolve += 1
        assert asset_ids == ("asset-1",)
        return (
            ManagedResearchAssetSnapshot(
                asset_id="asset-1",
                exact_reference="opaque-asset-1",
                size_bytes=1,
                purpose="dataset",
                completed=True,
                state_version=1,
                completed_at="2026-08-09T00:00:00+00:00",
                etag=None,
                version_id=None,
                last_modified=None,
                snapshot_digest="snapshot-1",
            ),
        )

    def launch(_request: ResearchHttpAdmissionInput, _outcome: Any) -> None:
        calls.launch += 1

    preflight = ResearchRoutePreflight(
        store=store,
        parser=parse,
        managed_snapshot_resolver=resolve,
        worker_launcher=launch,
    )

    first = await preflight.admit(_request())
    replay = await preflight.admit(_request())

    assert first.run_id == replay.run_id
    assert first.replay is False
    assert replay.replay is True
    assert calls.parse == 1
    assert calls.resolve == 1
    assert calls.launch == 1


async def test_nonconversation_http_request_requires_idempotency_key(
    tmp_path: Path,
) -> None:
    """A direct HTTP call cannot reserve a run without its key."""
    preflight = ResearchRoutePreflight(store=_store(tmp_path))

    with pytest.raises(ValueError) as caught:
        await preflight.admit(_request(key=None))

    assert getattr(caught.value, "code", None) == (
        "research_idempotency_key_required"
    )


def test_research_http_admission_input_is_opaque_and_typed() -> None:
    """The adapter contract carries IDs/options, never projected paths."""
    request = _request()
    assert request.managed_asset_ids == ("asset-1",)
    assert not hasattr(request, "data_list")
    assert request.route_source == "native"


async def test_native_research_skips_generic_background_reservation(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Research admission owns its root row.

    It never reserves a generic run.
    """

    def forbidden_reservation(**_kwargs: Any) -> Any:
        raise AssertionError("generic background reservation was called")

    monkeypatch.setattr(
        api_app_module,
        "reserve_background_submission",
        forbidden_reservation,
    )
    headers = {
        "Authorization": f"Bearer {issued_api_key}",
        "Idempotency-Key": "native-research-1",
    }

    first = await api_client.post(
        "/v1/agents/research/runs",
        headers=headers,
        json={"arguments": {"user_query": "summarize inputs"}},
    )
    replay = await api_client.post(
        "/v1/agents/research/runs",
        headers=headers,
        json={"arguments": {"user_query": "summarize inputs"}},
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert first.json()["status"] == "running"


async def test_nonempty_native_research_data_block_has_no_resolution_io(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy projected inputs fail before attachment resolution or storage."""

    def forbidden_resolution(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("attachment resolution was called")

    monkeypatch.setattr(
        "mcp_server_phytomni.api.routes.agents.resolve_attachment_input",
        forbidden_resolution,
    )
    response = await api_client.post(
        "/v1/agents/research/runs",
        headers={
            "Authorization": f"Bearer {issued_api_key}",
            "Idempotency-Key": "invalid-research-block",
        },
        json={
            "arguments": {
                "user_query": "summarize inputs",
                "data_list": {"obs://private/path.csv": ""},
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "research_data_block_invalid"
