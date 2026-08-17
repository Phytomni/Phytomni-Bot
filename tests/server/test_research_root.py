# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the repository-owned HTTP Research root composition."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.api import research_root

pytestmark = pytest.mark.server


class _MetadataPort:
    """Structural metadata port accepted by the root factory."""

    async def resolve(self, _request: Any) -> tuple[Any, ...]:
        """Return no authorities for this composition-only test."""
        return ()

    async def verify(self, _request: Any) -> tuple[Any, ...]:
        """Return no authorities for this composition-only test."""
        return ()

    async def revoke(self, _request: Any) -> None:
        """Accept revocation without external storage."""
        return None


def test_default_root_factory_keeps_empty_remote_inspection_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted query keeps descriptions empty for remote inspection."""

    limits = SimpleNamespace(
        API_MAX_ATTACHMENTS_PER_REQUEST=4,
        API_MAX_RESEARCH_DATASET_PATHS=8,
        API_MAX_RESEARCH_INPUT_REFERENCES=12,
    )
    source = SimpleNamespace(
        BUCKET_NAME="research-bucket",
        OBS_SERVER="https://obs.example.invalid",
    )
    monkeypatch.setattr(research_root, "ApiLimitsConfig", lambda: limits)
    monkeypatch.setattr(research_root, "ServerConfig", lambda: source)

    admission = cast(
        Any,
        SimpleNamespace(
            owner="owner-1",
            parsed_input=parse_research_input("", "research-bucket"),
            managed_snapshot=(),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
        ),
    )
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )
    request = factory(admission)

    assert request.effective_query == ""
    assert request.inventory_request.parsed_input.effective_query == ""

    captured: dict[str, Any] = {}

    def join(inventory: Any, *, effective_query: str) -> str:
        captured["inventory"] = inventory
        captured["effective_query"] = effective_query
        return "prepared"

    monkeypatch.setattr(
        research_root,
        "prepare_research_input_for_remote_inspection",
        join,
    )
    join_prepared = request.dependencies.join_prepared
    assert join_prepared is not None
    assert join_prepared("inventory", "resolution") == "prepared"
    assert captured == {
        "inventory": "inventory",
        "effective_query": "",
    }

    resolve_descriptions = request.dependencies.resolve_descriptions
    plan_builder = request.dependencies.plan_builder
    assert resolve_descriptions is not None
    assert plan_builder is not None

    async def check_resolution() -> None:
        assert await resolve_descriptions(request) is None

    asyncio.run(check_resolution())


def test_root_factory_rejects_incomplete_metadata_port() -> None:
    """The factory fails closed when the metadata port is incomplete."""

    class _PartialPort:
        resolve = None
        verify = None
        revoke = None

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_PartialPort"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    with pytest.raises(TypeError, match="metadata"):
        research_root.build_default_research_root_request_factory(
            metadata_port=cast(Any, _PartialPort()),
            asset_resolver_factory=None,
        )


def test_managed_snapshot_requires_resolver_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed assets cannot be admitted without a bound resolver."""
    monkeypatch.setattr(
        research_root,
        "ApiLimitsConfig",
        lambda: SimpleNamespace(
            API_MAX_ATTACHMENTS_PER_REQUEST=4,
            API_MAX_RESEARCH_DATASET_PATHS=8,
            API_MAX_RESEARCH_INPUT_REFERENCES=12,
        ),
    )
    monkeypatch.setattr(
        research_root,
        "ServerConfig",
        lambda: SimpleNamespace(BUCKET_NAME="research-bucket"),
    )
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()), asset_resolver_factory=None
    )
    admission = cast(
        Any,
        SimpleNamespace(
            owner="owner-1",
            parsed_input=parse_research_input("q", "research-bucket"),
            managed_snapshot=(object(),),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
        ),
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        factory(admission)


def test_bind_default_factory_requires_runtime_and_port() -> None:
    """Binding fails closed when the production runtime is incomplete."""
    with pytest.raises(RuntimeError, match="not registered"):
        research_root.bind_default_research_root_request_factory(
            object(), None, asset_resolver_factory=None
        )
    with pytest.raises(RuntimeError, match="metadata port"):
        research_root.bind_default_research_root_request_factory(
            SimpleNamespace(),
            SimpleNamespace(root_request_factory=None),
            asset_resolver_factory=None,
        )

    @dataclass
    class _Runtime:
        root_request_factory: object = None

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_Runtime"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    bound = research_root.bind_default_research_root_request_factory(
        SimpleNamespace(metadata_port=_MetadataPort()),
        _Runtime(),
        asset_resolver_factory=None,
    )
    assert callable(bound.root_request_factory)


def test_direct_goal_downloader_and_converter_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Goal, download, and conversion ports stay bounded."""
    provider = getattr(research_root, "_DirectGoalProvider")(
        "Inspect the files."
    )

    async def _extract() -> None:
        assert (await provider.extract(object(), "en-US"))[
            0
        ].goal == "Inspect the files."

    asyncio.run(_extract())
    downloader = getattr(research_root, "_ManagedDocumentDownloader")(
        cast(Any, SimpleNamespace(BUCKET_NAME="research-bucket"))
    )
    entry = SimpleNamespace(exact_reference="obs://research-bucket/a.pdf")
    assert (
        downloader.observe(
            SimpleNamespace(
                exact_reference=entry.exact_reference, snapshot="s"
            )
        ).exact_reference
        == entry.exact_reference
    )

    class _Relay:
        async def get_obs_object(self, _ref: str, *, message: str) -> bytes:
            """Return scripted relay bytes."""
            del message
            return b"relay-bytes"

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_Relay"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    monkeypatch.setattr(research_root, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(research_root, "current_relay_client", _Relay)

    async def _relay() -> None:
        assert await downloader.download(entry) == b"relay-bytes"

    asyncio.run(_relay())

    class _Obs:
        async def run(self, _p: object, callback: Any) -> bytes:
            """Invoke the download callback with a dummy client."""
            return await callback(object())

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_Obs"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    monkeypatch.setattr(research_root, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(research_root, "current_obs_runtime", _Obs)

    async def _bytes(*_a: Any, **_k: Any) -> bytes:
        return b"direct-bytes"

    monkeypatch.setattr(research_root, "get_object_bytes", _bytes)

    async def _direct() -> None:
        assert await downloader.download(entry) == b"direct-bytes"

    asyncio.run(_direct())
    converter = getattr(research_root, "_MarkItDownDocumentConverter")()
    monkeypatch.setattr(
        research_root,
        "convert_single_file",
        lambda _p, cleanup=False: "page-a\f\npage-b",
    )
    pages = converter.convert(
        SimpleNamespace(compound_suffix=".pdf", safe_basename="paper.pdf"),
        b"%PDF",
    )
    assert [page.label for page in pages] == ["page-1", "page-2"]
    monkeypatch.setattr(
        research_root,
        "convert_single_file",
        lambda _p, cleanup=False: "only section",
    )
    assert (
        converter.convert(
            SimpleNamespace(compound_suffix=".txt", safe_basename="notes.txt"),
            b"t",
        )[0].label
        == "section-1"
    )
    monkeypatch.setattr(
        research_root, "convert_single_file", lambda *_a, **_k: "   "
    )
    with pytest.raises(ValueError, match="no text"):
        converter.convert(
            SimpleNamespace(compound_suffix=".txt", safe_basename="notes.txt"),
            b"e",
        )


def test_root_factory_closures_and_managed_resolver_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory, evidence, revalidate, and plan closures stay injectable."""
    monkeypatch.setattr(
        research_root,
        "ApiLimitsConfig",
        lambda: SimpleNamespace(
            API_MAX_ATTACHMENTS_PER_REQUEST=4,
            API_MAX_RESEARCH_DATASET_PATHS=8,
            API_MAX_RESEARCH_INPUT_REFERENCES=12,
        ),
    )
    monkeypatch.setattr(
        research_root,
        "ServerConfig",
        lambda: SimpleNamespace(BUCKET_NAME="research-bucket"),
    )
    bound: dict[str, Any] = {}
    monkeypatch.setattr(
        research_root,
        "bind_research_asset_resolver",
        lambda **kw: bound.setdefault("resolver", kw),
    )
    captured: dict[str, Any] = {}

    async def _inventory(request: Any, port: Any) -> str:
        """Record the inventory closure arguments."""
        captured["inventory"] = (request, port)
        return "inventory"

    async def _evidence(*_args: Any, **_kwargs: Any) -> str:
        """Return a stub evidence payload."""
        return "evidence"

    async def _revalidate(*_args: Any, **_kwargs: Any) -> str:
        """Return a stub revalidation payload."""
        return "revalidated"

    async def _plan(request: Any, provider: Any) -> str:
        captured["plan"] = (request, provider)
        return "plan"

    monkeypatch.setattr(research_root, "build_research_inventory", _inventory)
    monkeypatch.setattr(research_root, "extract_research_evidence", _evidence)
    monkeypatch.setattr(
        research_root, "revalidate_research_inventory", _revalidate
    )
    monkeypatch.setattr(research_root, "build_research_plan", _plan)

    def _resolver_factory() -> object:
        bound["factory"] = True
        return object()

    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=_resolver_factory,
    )
    request = factory(
        cast(
            Any,
            SimpleNamespace(
                owner="owner-1",
                parsed_input=parse_research_input("query", "research-bucket"),
                managed_snapshot=(object(),),
                locale="en-US",
                interop_mode="off",
                interop_targets=(),
            ),
        )
    )
    assert bound["factory"] is True and bound["resolver"]["owner"] == "owner-1"

    build_inventory = request.dependencies.build_inventory
    extract_evidence = request.dependencies.extract_evidence
    revalidate_inventory = request.dependencies.revalidate_inventory
    plan_builder = request.dependencies.plan_builder
    assert build_inventory is not None
    assert extract_evidence is not None
    assert revalidate_inventory is not None
    assert plan_builder is not None

    async def _run() -> None:
        assert await build_inventory(request) == "inventory"
        assert await extract_evidence(request) == "evidence"
        assert await revalidate_inventory(request) == "revalidated"
        assert (
            await plan_builder(
                SimpleNamespace(effective_query="  Analyze genes.  "), request
            )
            == "plan"
        )
        await plan_builder(SimpleNamespace(effective_query="   "), request)
        assert (
            captured["plan"][1].goal == "Analyze the supplied research inputs."
        )

    asyncio.run(_run())
