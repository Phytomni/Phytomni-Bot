# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Synthetic resumable assets shared by upload and HTTP contract tests."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.asset_resolver import AssetResolver
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.resumable_uploads import (
    ResumableUploadService,
    UploadServiceConfig,
)
from mcp_server_phytomni.api.schemas import (
    UploadCompletionRequest,
    UploadCreateRequest,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.resumable_uploads import (
    ResumableUploadRegistry,
    UploadAssetPurpose,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.submit_recorder import records_submission
from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    PartInput,
)
from mcp_server_phytomni.storage.obs_storage import obs_path_from_key
from tests.support.http_fakes import (
    install_rejection_handler,
    install_tool_handler,
    open_asgi_client,
)


@dataclass(frozen=True)
class ResumableAssetHarness:
    """One opaque asset plus the real service and resolver behind it."""

    resolver: AssetResolver
    service: ResumableUploadService
    asset_id: str
    owner: str
    content: bytes
    capability: str


@dataclass(frozen=True, slots=True)
class ResumableAssetSpec:
    """Configurable trusted input for one synthetic resumable asset."""

    owner: str = "owner-1"
    filename: str = "context.pdf"
    content: bytes = b"synthetic attachment"
    complete: bool = True
    purpose: UploadAssetPurpose = "chat_attachment"


def build_resumable_asset(
    _tmp_path: Path,
    /,
    *,
    db_path: str | None = None,
    spec: ResumableAssetSpec = ResumableAssetSpec(),
) -> ResumableAssetHarness:
    """Build one owner-scoped resumable asset without external storage.

    The temporary root is positional-only; every behavior knob remains an
    explicit keyword-only argument while the helper stays within the
    repository's five-argument static-analysis ceiling.
    """
    tmp_path = _tmp_path
    registry = ResumableUploadRegistry(
        db_path or str(tmp_path / "uploads.sqlite")
    )
    storage = FakeMultipartStorage()
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="resolver-bucket",
            upload_origin="https://upload.example",
        ),
    )
    digest = hashlib.sha256(spec.content).hexdigest()
    identity = hashlib.sha256(
        spec.owner.encode("utf-8")
        + b"\0"
        + spec.filename.encode("utf-8")
        + b"\0"
        + spec.content
        + b"\0"
        + spec.purpose.encode("utf-8")
    ).hexdigest()
    created = service.create(
        UploadCreateRequest(
            owner_subject=spec.owner,
            filename=spec.filename,
            content_type="application/octet-stream",
            size_bytes=len(spec.content),
            purpose=spec.purpose,
            idempotency_key=f"resolver-{identity}",
        )
    )
    if spec.complete:
        service.put_part(
            created.asset_id,
            created.capability,
            PartInput(1, BytesIO(spec.content), len(spec.content), digest),
        )
        service.complete(
            created.asset_id,
            created.capability,
            UploadCompletionRequest(),
        )
    resolver = AssetResolver(
        registry,
        storage.download_to_path,
        bucket_name="resolver-bucket",
        workspace_root=tmp_path / "materialized",
    )
    return ResumableAssetHarness(
        resolver=resolver,
        service=service,
        asset_id=created.asset_id,
        owner=spec.owner,
        content=spec.content,
        capability=created.capability,
    )


@dataclass(frozen=True)
class BackgroundAssetCase:
    """Shared data contract for one synthetic remote-Agent response."""

    slug: str
    tool_name: str
    stub_return: dict[str, Any]
    arguments: dict[str, Any]
    expected_task_ids: set[str]


@dataclass(frozen=True)
class AssetHttpTestContext:
    """Shared pytest state for an opaque-asset native run."""

    monkeypatch: pytest.MonkeyPatch
    tmp_path: Path
    db_path: str
    api_key: str


@dataclass(frozen=True)
class AssetRejectionCase:
    """One opaque-asset rejection with its public HTTP outcome."""

    scenario: str
    status_code: int
    safe_code: str


def install_attachment_capture(
    monkeypatch: pytest.MonkeyPatch,
    case: BackgroundAssetCase,
    captured: dict[str, Any],
) -> None:
    """Install one recorder-aware handler that captures typed arguments."""

    async def fake(arguments: Any) -> dict[str, Any]:
        captured["arguments"] = arguments
        return case.stub_return

    handler = records_submission(case.slug)(fake)
    install_tool_handler(monkeypatch, case.tool_name, handler)


async def wait_for_attachment_submission(
    *,
    captured: dict[str, Any],
    db_path: str,
    run_id: str,
    case: BackgroundAssetCase,
) -> Any:
    """Wait a bounded number of yields for capture and child persistence."""
    registry = RunRegistry(db_path)
    for _ in range(100):
        arguments = captured.get("arguments")
        record = registry.get_run(run_id, owner="u1")
        if (
            arguments is not None
            and record is not None
            and set(record.task_ids) == case.expected_task_ids
        ):
            return arguments
        await asyncio.sleep(0)
    pytest.fail("background attachment submission did not settle")


async def execute_opaque_asset_run(
    context: AssetHttpTestContext,
    case: BackgroundAssetCase,
) -> tuple[ResumableAssetHarness, httpx.Response, Any]:
    """Run one completed owner asset through the native HTTP boundary."""
    harness = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(owner="u1"),
    )
    captured: dict[str, Any] = {}
    install_attachment_capture(context.monkeypatch, case, captured)
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: harness.resolver,
    )
    async with open_asgi_client(
        context.monkeypatch,
        api_app_module.create_app(),
        base_url="http://api.asset.test",
    ) as client:
        response = await client.post(
            f"/v1/agents/{case.slug}/runs",
            headers={"Authorization": f"Bearer {context.api_key}"},
            json={
                "arguments": case.arguments,
                "attachments": [{"asset_id": harness.asset_id}],
            },
        )
    if response.status_code != 202:
        return harness, response, None
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    return harness, response, arguments


def _rejection_asset(
    context: AssetHttpTestContext,
    scenario: str,
) -> tuple[ResumableAssetHarness, list[dict[str, str]], tuple[str, ...]]:
    """Build one rejection input plus the private values it must redact."""
    harness = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner="other-user" if scenario == "foreign" else "u1",
            filename="private-authorization-sentinel.pdf",
            content=b"authorization-sentinel",
            complete=scenario != "incomplete",
        ),
    )
    requested_id = "not/valid" if scenario == "malformed" else harness.asset_id
    attachments = [{"asset_id": requested_id}]
    if scenario == "duplicate":
        attachments.append({"asset_id": requested_id})
    asset = harness.service.registry.get_asset(
        harness.asset_id,
        owner=harness.owner,
    )
    assert asset is not None
    internal_reference = obs_path_from_key(
        harness.service.bucket_name,
        asset.object_key,
    )
    private_values = (
        harness.asset_id,
        requested_id,
        asset.filename,
        asset.object_key,
        harness.capability,
        "authorization-sentinel",
        internal_reference,
    )
    return harness, attachments, private_values


async def execute_rejected_asset_run(
    context: AssetHttpTestContext,
    case: BackgroundAssetCase,
    scenario: str,
) -> tuple[httpx.Response, tuple[str, ...], bool]:
    """Run one unsafe opaque asset and return only safe test observations."""
    harness, attachments, private_values = _rejection_asset(context, scenario)
    marker = install_rejection_handler(context.monkeypatch, case.tool_name)
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: harness.resolver,
    )
    async with open_asgi_client(
        context.monkeypatch,
        api_app_module.create_app(),
        base_url="http://api.asset.test",
    ) as client:
        response = await client.post(
            f"/v1/agents/{case.slug}/runs",
            headers={"Authorization": f"Bearer {context.api_key}"},
            json={"arguments": case.arguments, "attachments": attachments},
        )
    return response, private_values, marker["called"]


def enable_conversation_context_v1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    user_id: str = "u1",
) -> tuple[AssetHttpTestContext, str]:
    """Enable Instant V1 context and return one owner API key plus context."""
    monkeypatch.setenv("PHYTOMNI_CONVERSATION_CONTEXT_V1_ENABLED", "1")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    api_key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(user_id=user_id)
        .api_key
    )
    context = AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=str(tmp_path / "tasks.sqlite"),
        api_key=api_key,
    )
    return context, api_key


def install_dataset_and_document_assets(
    context: AssetHttpTestContext,
    *,
    owner: str = "u1",
    dataset_filename: str = "input.csv",
    document_filename: str = "context.pdf",
    document_purpose: UploadAssetPurpose = "document",
) -> tuple[Any, str, str]:
    """Install one dataset and one document under the shared resolver."""
    dataset = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename=dataset_filename,
            content=b"gene,value\nAT1G01010,1\n",
            purpose="dataset",
        ),
    )
    document = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename=document_filename,
            content=b"%PDF-1.4\ncontext\n",
            purpose=document_purpose,
        ),
    )
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: dataset.resolver,
    )
    return dataset.resolver, dataset.asset_id, document.asset_id
