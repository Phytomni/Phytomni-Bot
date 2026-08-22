# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for HTTP Research root composition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.server.test_research_root import _MetadataPort

from mcp_server_phytomni.agents.research.document_evidence import (
    ConvertedResearchSection,
    ManagedDocumentPayload,
)
from mcp_server_phytomni.agents.research.input_parser import (
    parse_research_input,
)
from mcp_server_phytomni.api import research_root
from mcp_server_phytomni.storage.downloads import ResolvedObsFile

pytestmark = pytest.mark.server


def _limits() -> SimpleNamespace:
    """Return the API-limit namespace used by the factory."""
    return SimpleNamespace(
        API_MAX_ATTACHMENTS_PER_REQUEST=4,
        API_MAX_RESEARCH_DATASET_PATHS=8,
        API_MAX_RESEARCH_INPUT_REFERENCES=12,
    )


def _source(tmp_path: Path | None = None) -> SimpleNamespace:
    """Return the storage-config namespace used by the factory."""
    return SimpleNamespace(
        BUCKET_NAME="research-bucket",
        OBS_SERVER="https://obs.example.invalid",
        TEMP_DIR=str(tmp_path) if tmp_path is not None else ".",
    )


def _admission(**overrides: Any) -> Any:
    """Build one admission namespace with optional field overrides."""
    values: dict[str, Any] = {
        "owner": "owner-1",
        "parsed_input": parse_research_input(
            "Analyze rice drought genes",
            "research-bucket",
        ),
        "managed_snapshot": (),
        "locale": "en-US",
        "interop_mode": "off",
        "interop_targets": (),
    }
    values.update(overrides)
    return cast(Any, SimpleNamespace(**values))


def _install_factory_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin factory config construction to offline namespaces."""
    monkeypatch.setattr(research_root, "ApiLimitsConfig", _limits)
    monkeypatch.setattr(research_root, "ServerConfig", _source)


async def test_direct_goal_provider_returns_bounded_goal() -> None:
    """The HTTP goal provider keeps the supplied query as one goal."""
    provider = getattr(research_root, "_DirectGoalProvider")(
        "Find drought genes"
    )

    assert provider.contract_name == "research_goal_provider"
    goals = await provider.extract(evidence="ignored", locale="zh-CN")

    assert len(goals) == 1
    assert goals[0].goal == "Find drought genes"


def test_managed_downloader_observes_inventory_snapshot() -> None:
    """Observation copies the fenced inventory reference and snapshot."""
    downloader = getattr(research_root, "_ManagedDocumentDownloader")(
        cast(Any, _source())
    )
    entry = SimpleNamespace(
        exact_reference="owner/docs/brief.pdf",
        snapshot={"etag": "abc"},
    )

    assert downloader.contract_name == "managed_document_downloader"
    observation = downloader.observe(entry)

    assert observation.exact_reference == "owner/docs/brief.pdf"
    assert observation.snapshot == {"etag": "abc"}


async def test_managed_downloader_uses_shared_obs_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The HTTP adapter stages OBS objects through the shared downloader."""
    staged = tmp_path / "brief.pdf"
    staged.write_bytes(b"pdf-bytes")
    captured: dict[str, Any] = {}

    async def _download_source(
        obs_file: str, server_dir: str, **kwargs: Any
    ) -> ResolvedObsFile:
        captured["obs_file"] = obs_file
        captured["server_dir"] = server_dir
        captured["kwargs"] = kwargs
        return ResolvedObsFile(file_path=str(staged), cleanup=True)

    monkeypatch.setattr(research_root, "download_obs_source", _download_source)
    downloader = getattr(research_root, "_ManagedDocumentDownloader")(
        cast(Any, _source(tmp_path))
    )

    payload = await downloader.download(
        SimpleNamespace(exact_reference="owner/docs/brief.pdf")
    )

    assert payload == ManagedDocumentPayload(
        path=str(staged), size_bytes=9, cleanup=True
    )
    assert captured["obs_file"] == "owner/docs/brief.pdf"
    assert captured["server_dir"] == str(tmp_path)
    assert captured["kwargs"]["bucket_name"] == "research-bucket"


def test_document_converter_splits_pdf_pages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PDF conversion keeps form-feed units as numbered pages."""

    def _convert(path: str, cleanup: bool = False) -> Any:
        del path, cleanup
        return SimpleNamespace(sections=("page-one", "page-two"))

    monkeypatch.setattr(research_root, "convert_document_file", _convert)
    converter = getattr(research_root, "_MarkItDownDocumentConverter")()
    entry = SimpleNamespace(
        compound_suffix=".pdf",
        safe_basename="Brief.PDF",
    )
    payload = ManagedDocumentPayload(
        path=str(tmp_path / "brief.pdf"), size_bytes=8
    )

    assert converter.contract_name == "managed_document_converter"
    sections = converter.convert(entry, payload)

    assert sections == (
        ConvertedResearchSection(1, "page-1", "page-one"),
        ConvertedResearchSection(2, "page-2", "page-two"),
    )


def test_document_converter_labels_non_pdf_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-PDF conversion uses section labels and rejects empty text."""

    def _convert(path: str, cleanup: bool = False) -> Any:
        del path, cleanup
        return SimpleNamespace(sections=("only-section",))

    monkeypatch.setattr(research_root, "convert_document_file", _convert)
    converter = getattr(research_root, "_MarkItDownDocumentConverter")()
    entry = SimpleNamespace(
        compound_suffix=".md",
        safe_basename="notes.md",
    )
    payload = ManagedDocumentPayload(
        path=str(tmp_path / "notes.md"), size_bytes=5
    )

    sections = converter.convert(entry, payload)
    assert sections == (
        ConvertedResearchSection(1, "section-1", "only-section"),
    )

    def _empty(path: str, cleanup: bool = False) -> Any:
        del path, cleanup
        raise ValueError("document conversion returned no text")

    monkeypatch.setattr(research_root, "convert_document_file", _empty)
    with pytest.raises(ValueError, match="returned no text"):
        converter.convert(entry, payload)


def test_research_root_does_not_buffer_document_bytes() -> None:
    """HTTP Research composition must not join OBS objects into bytes."""
    source = Path(research_root.__file__).read_text(encoding="utf-8")
    assert "get_object_bytes" not in source
    assert "get_obs_object(" not in source


def test_factory_rejects_incomplete_metadata_port() -> None:
    """A metadata port missing the contract methods fails closed."""
    with pytest.raises(TypeError, match="must implement Research object"):
        research_root.build_default_research_root_request_factory(
            metadata_port=cast(
                Any,
                SimpleNamespace(resolve=None, verify=None, revoke=None),
            ),
            asset_resolver_factory=None,
        )


def test_factory_requires_resolver_for_managed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed attachments cannot bind without a resolver factory."""
    _install_factory_config(monkeypatch)
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )

    with pytest.raises(RuntimeError, match="resolver is unavailable"):
        factory(_admission(managed_snapshot=("asset-1",)))


def test_factory_binds_managed_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A managed snapshot binds the owner-scoped asset resolver."""
    _install_factory_config(monkeypatch)
    captured: dict[str, Any] = {}

    def _bind(*, owner: str, resolver: Any) -> str:
        captured["owner"] = owner
        captured["resolver"] = resolver
        return "bound-resolver"

    monkeypatch.setattr(research_root, "bind_research_asset_resolver", _bind)
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=lambda: "resolver-impl",
    )

    request = factory(_admission(managed_snapshot=("asset-1",)))

    assert request.run_id == "research-http-root"
    assert captured == {"owner": "owner-1", "resolver": "resolver-impl"}


async def test_factory_inventory_and_evidence_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory and evidence closures forward the admission snapshot."""
    _install_factory_config(monkeypatch)
    seen: dict[str, Any] = {}

    async def _inventory(request: Any, port: Any) -> str:
        seen["inventory"] = (request, port)
        return "inventory"

    async def _evidence(request: Any, downloader: Any, converter: Any) -> str:
        seen["evidence"] = (request.effective_query, downloader, converter)
        return "evidence"

    monkeypatch.setattr(research_root, "build_research_inventory", _inventory)
    monkeypatch.setattr(research_root, "extract_research_evidence", _evidence)
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )
    request = factory(_admission())
    inventory_port = request.dependencies.build_inventory
    evidence_port = request.dependencies.extract_evidence
    assert inventory_port is not None
    assert evidence_port is not None

    assert await inventory_port(request) == "inventory"
    assert await evidence_port(request) == "evidence"
    assert seen["inventory"][1].__class__ is _MetadataPort
    assert seen["evidence"][0] == "Analyze rice drought genes"


async def test_factory_revalidate_and_plan_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revalidate and plan closures use the bound inventory and goal."""
    _install_factory_config(monkeypatch)
    seen: dict[str, Any] = {}

    async def _revalidate(
        inventory_request: Any,
        live_request: Any,
        port: Any,
        managed_asset_resolver: Any = None,
    ) -> str:
        seen["revalidate"] = (
            inventory_request,
            live_request,
            port,
            managed_asset_resolver,
        )
        return "revalidated"

    async def _plan(request: Any, provider: Any) -> str:
        seen["plan"] = (request.run_id, provider.goal, provider.contract_name)
        return "planned"

    monkeypatch.setattr(
        research_root, "revalidate_research_inventory", _revalidate
    )
    monkeypatch.setattr(research_root, "build_research_plan", _plan)
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )
    request = factory(_admission())
    revalidate = request.dependencies.revalidate_inventory
    plan_builder = request.dependencies.plan_builder
    assert revalidate is not None
    assert plan_builder is not None

    assert await revalidate(request) == "revalidated"
    prepared = SimpleNamespace(effective_query="  compare   cultivars  ")
    assert await plan_builder(prepared, request) == "planned"
    assert seen["plan"] == (
        "research-http-root",
        "compare cultivars",
        "research_goal_provider",
    )


async def test_plan_builder_falls_back_when_query_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blank prepared query still yields one bounded default goal."""
    _install_factory_config(monkeypatch)
    seen: dict[str, str] = {}

    async def _plan(_request: Any, provider: Any) -> str:
        seen["goal"] = provider.goal
        return "planned"

    monkeypatch.setattr(research_root, "build_research_plan", _plan)
    factory = research_root.build_default_research_root_request_factory(
        metadata_port=cast(Any, _MetadataPort()),
        asset_resolver_factory=None,
    )
    request = factory(_admission())
    plan_builder = request.dependencies.plan_builder
    assert plan_builder is not None

    assert await plan_builder(SimpleNamespace(effective_query="   "), request)
    assert seen["goal"] == "Analyze the supplied research inputs."


def test_bind_default_factory_requires_runtime() -> None:
    """Binding fails when the production runtime was never registered."""
    with pytest.raises(RuntimeError, match="was not registered"):
        research_root.bind_default_research_root_request_factory(
            coordinator=object(),
            runtime=None,
            asset_resolver_factory=None,
        )


def test_bind_default_factory_requires_metadata_port() -> None:
    """Binding fails when the coordinator has no metadata port."""
    with pytest.raises(RuntimeError, match="no metadata port"):
        research_root.bind_default_research_root_request_factory(
            coordinator=SimpleNamespace(),
            runtime=object(),
            asset_resolver_factory=None,
        )


def test_bind_default_factory_replaces_runtime_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy bind attaches the composed factory onto runtime state."""
    _install_factory_config(monkeypatch)

    @dataclass(frozen=True)
    class _Runtime:
        """Minimal replaceable runtime carrier."""

        root_request_factory: Any = None

    bound = research_root.bind_default_research_root_request_factory(
        coordinator=SimpleNamespace(metadata_port=_MetadataPort()),
        runtime=_Runtime(),
        asset_resolver_factory=None,
    )

    assert callable(bound.root_request_factory)
