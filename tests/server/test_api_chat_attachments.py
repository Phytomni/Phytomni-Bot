# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat document-only attachment resolution and response redaction tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.support.chat_fakes import install_chat_handler
from tests.support.handler_fakes import review_success_result
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    install_tool_handler,
    open_asgi_client,
)
from tests.support.resumable_asset_fakes import (
    AssetHttpTestContext,
    ResumableAssetSpec,
    build_resumable_asset,
    enable_conversation_context_v1,
)

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.resumable_uploads import UploadAssetPurpose
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _install_chat_asset(
    context: AssetHttpTestContext,
    *,
    purpose: UploadAssetPurpose,
    filename: str,
    content: bytes,
    owner: str = "u1",
) -> Any:
    """Install one completed Chat asset and wire its resolver into the app."""
    harness = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename=filename,
            content=content,
            purpose=purpose,
        ),
    )
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: harness.resolver,
    )
    return harness


def _document_reference(harness: Any, owner: str = "u1") -> str:
    """Resolve the managed document reference for one completed asset."""
    return (
        harness.resolver.resolve_bundle(
            [{"asset_id": harness.asset_id}], owner
        )
        .documents[0]
        .reference
    )


@pytest.mark.parametrize(
    "purpose",
    (
        "document",
        "chat_attachment",
    ),
)
async def test_chat_document_assets_resolve_and_invoke(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
    purpose: UploadAssetPurpose,
) -> None:
    """Completed document-purpose assets resolve into Chat document context."""
    harness = _install_chat_asset(
        asset_http_context,
        purpose=purpose,
        filename="chat.pdf",
        content=b"%PDF-1.4\nchat\n",
    )
    reference = (
        harness.resolver.resolve_bundle([{"asset_id": harness.asset_id}], "u1")
        .assets[0]
        .reference
    )
    captured: dict[str, Any] = {}
    install_chat_handler(
        asset_http_context.monkeypatch,
        captured,
        content=f"saw {reference}",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.chat-doc.test",
    ) as client:
        response = await chat_completion(
            client,
            asset_http_context.api_key,
            content="summarize the attachment",
            attachments=[{"asset_id": harness.asset_id}],
        )
    assert response.status_code == 200, response.text
    assert captured["obs_file_list"] == [reference]
    assert harness.asset_id not in response.text
    assert reference not in response.text
    assert "<redacted-attachment>" in response.text


@pytest.mark.parametrize(
    ("model", "tool_name"),
    (
        ("phyto-chat", "ChatAgent"),
        ("phyto-knowledge", "KnowledgeAgent"),
    ),
)
async def test_chat_compatible_models_project_mixed_assets(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
    model: str,
    tool_name: str,
) -> None:
    """Chat-compatible models receive every managed class in source order."""
    dataset = _install_chat_asset(
        asset_http_context,
        purpose="dataset",
        filename="chat.csv",
        content=b"a,b\n1,2\n",
    )
    document = _install_chat_asset(
        asset_http_context,
        purpose="document",
        filename="chat.pdf",
        content=b"%PDF-1.4\nchat\n",
    )
    references = [
        asset.reference
        for asset in dataset.resolver.resolve_bundle(
            [
                {"asset_id": dataset.asset_id},
                {"asset_id": document.asset_id},
            ],
            "u1",
        ).assets
    ]
    captured: dict[str, Any] = {}

    async def capture(args: Any) -> dict[str, Any]:
        captured["obs_file_list"] = args.obs_file_list
        return {"answer": "ok", "doc_list": []}

    install_tool_handler(asset_http_context.monkeypatch, tool_name, capture)
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.chat-compatible.test",
    ) as client:
        response = await chat_completion(
            client,
            asset_http_context.api_key,
            model=model,
            content="analyze this csv",
            attachments=[
                {"asset_id": dataset.asset_id},
                {"asset_id": document.asset_id},
            ],
        )
    assert response.status_code == 200, response.text
    assert captured["obs_file_list"] == references


async def test_review_projects_mixed_managed_assets_as_documents(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
) -> None:
    """Review receives dataset and document assets through obs_file_list."""
    dataset = _install_chat_asset(
        asset_http_context,
        purpose="dataset",
        filename="review.csv",
        content=b"a,b\n1,2\n",
    )
    document = _install_chat_asset(
        asset_http_context,
        purpose="document",
        filename="review.pdf",
        content=b"%PDF-1.4\nreview\n",
    )
    references = [
        asset.reference
        for asset in dataset.resolver.resolve_bundle(
            [
                {"asset_id": dataset.asset_id},
                {"asset_id": document.asset_id},
            ],
            "u1",
        ).assets
    ]
    captured: dict[str, Any] = {}

    async def fake_run_review(**kwargs: Any) -> ReviewExecution:
        captured["arguments"] = dict(kwargs["arguments"])
        return ReviewExecution(
            run_id="review-mixed-assets",
            status="succeeded",
            result=review_success_result(),
        )

    asset_http_context.monkeypatch.setattr(
        api_app_module, "_run_review_with_interrupt", fake_run_review
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.review-mixed-assets.test",
    ) as client:
        response = await chat_completion(
            client,
            asset_http_context.api_key,
            model="phyto-review",
            content="review mixed assets",
            attachments=[
                {"asset_id": dataset.asset_id},
                {"asset_id": document.asset_id},
            ],
        )
    assert response.status_code == 200, response.text
    assert captured["arguments"]["obs_file_list"] == references


@pytest.mark.parametrize("model", ("phyto-brief-gene",))
async def test_zero_channel_chat_models_fail_before_stream_or_sync_run(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
    model: str,
) -> None:
    """Zero-channel models reject assets before a stream or run exists."""
    harness = _install_chat_asset(
        asset_http_context,
        purpose="dataset",
        filename="unsupported.csv",
        content=b"a,b\n1,2\n",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.chat-zero-channel.test",
    ) as client:
        response = await chat_completion(
            client,
            asset_http_context.api_key,
            model=model,
            content="do not run",
            stream=True,
            attachments=[{"asset_id": harness.asset_id}],
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert response.headers["content-type"].startswith("application/json")
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


async def test_chat_owner_subject_requires_delegate_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit Chat owner assertions require files:delegate."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    agents_only = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(user_id="principal-owner", scopes=["agents"])
        .api_key
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.chat-owner.test"
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {agents_only}"},
            json={
                "model": "phyto-chat",
                "messages": [{"role": "user", "content": "hi"}],
                "owner_subject": "principal-owner",
            },
        )
    assert response.status_code == 403
    assert "files:delegate" not in response.text


async def test_chat_context_dataset_asset_projects_as_document_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    chat_completion: Callable[..., Any],
) -> None:
    """Context Chat projects dataset assets through its document channel."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    harness = _install_chat_asset(
        context,
        purpose="dataset",
        filename="ctx.csv",
        content=b"a,b\n1,2\n",
    )
    reference = (
        harness.resolver.resolve_bundle([{"asset_id": harness.asset_id}], "u1")
        .assets[0]
        .reference
    )
    captured: dict[str, Any] = {}
    install_chat_handler(
        monkeypatch,
        captured,
        content=f"context saw {reference}",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.chat-ctx-dataset.test"
    ) as client:
        response = await chat_completion(
            client,
            key,
            content="ignored",
            conversation=build_instant_chat_context_envelope("12"),
            attachments=[{"asset_id": harness.asset_id}],
        )
    assert response.status_code == 200, response.text
    assert captured["obs_file_list"] == [reference]


@pytest.mark.parametrize(
    "purpose",
    (
        "document",
        "chat_attachment",
    ),
)
async def test_chat_instant_context_document_assets_resolve_and_invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    chat_completion: Callable[..., Any],
    purpose: UploadAssetPurpose,
) -> None:
    """Instant context Chat document assets resolve before staging succeeds."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    harness = _install_chat_asset(
        context,
        purpose=purpose,
        filename="ctx.pdf",
        content=b"%PDF-1.4\nctx\n",
    )
    reference = _document_reference(harness)
    captured: dict[str, Any] = {}
    install_chat_handler(
        monkeypatch,
        captured,
        content=f"saw {reference}",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.chat-ctx-doc.test"
    ) as client:
        response = await chat_completion(
            client,
            key,
            content="ignored",
            conversation=build_instant_chat_context_envelope("13"),
            attachments=[{"asset_id": harness.asset_id}],
        )
    assert response.status_code == 200, response.text
    assert captured["obs_file_list"] == [reference]
    assert reference not in response.text
    assert "<redacted-attachment>" in response.text


async def test_chat_delegated_owner_resolves_asset_under_owner_subject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    chat_completion: Callable[..., Any],
) -> None:
    """Delegated Chat resolves under owner_subject; runs stay principal."""
    db_path = str(tmp_path / "tasks.sqlite")
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", db_path)
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    principal_key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(
            user_id="web-service",
            scopes=["agents", "files:delegate"],
        )
        .api_key
    )
    context = AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=db_path,
        api_key=principal_key,
    )
    harness = _install_chat_asset(
        context,
        purpose="document",
        filename="delegated.pdf",
        content=b"%PDF-1.4 delegated\n",
        owner="principal-owner",
    )
    reference = _document_reference(harness, owner="principal-owner")
    captured: dict[str, Any] = {}
    install_chat_handler(
        monkeypatch,
        captured,
        content=f"delegated saw {reference}",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.chat-delegated.test"
    ) as client:
        response = await chat_completion(
            client,
            principal_key,
            content="summarize delegated",
            attachments=[{"asset_id": harness.asset_id}],
            owner_subject="principal-owner",
        )
    assert response.status_code == 200, response.text
    assert captured["obs_file_list"] == [reference]
    assert reference not in response.text
    runs = RunRegistry(db_path).list_runs(owner="web-service")
    assert len(runs) == 1
    assert runs[0].spec.user_id == "web-service"
    assert not RunRegistry(db_path).list_runs(owner="principal-owner")


async def test_review_chat_completion_redacts_managed_document_reference(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
) -> None:
    """Review sync completions redact managed references in normal/debug."""
    harness = _install_chat_asset(
        asset_http_context,
        purpose="document",
        filename="review.pdf",
        content=b"%PDF-1.4 review\n",
    )
    reference = _document_reference(harness)
    captured: dict[str, Any] = {}

    async def fake_run_review(**kwargs: Any) -> ReviewExecution:
        captured["arguments"] = dict(kwargs["arguments"])
        result = review_success_result()
        result["formatted"]["answer"] = f"review uses {reference}"
        result["raw"] = {"echo": reference}
        return ReviewExecution(
            run_id="review-redact-probe",
            status="succeeded",
            result=result,
        )

    asset_http_context.monkeypatch.setattr(
        api_app_module, "_run_review_with_interrupt", fake_run_review
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.review-redact.test",
    ) as client:
        normal = await chat_completion(
            client,
            asset_http_context.api_key,
            model="phyto-review",
            content="review the attachment",
            attachments=[{"asset_id": harness.asset_id}],
        )
        debug = await chat_completion(
            client,
            asset_http_context.api_key,
            model="phyto-review",
            content="review the attachment",
            attachments=[{"asset_id": harness.asset_id}],
            debug=True,
        )
    assert captured["arguments"]["obs_file_list"] == [reference]
    for response in (normal, debug):
        assert response.status_code == 200, response.text
        assert reference not in response.text
        assert "<redacted-attachment>" in response.text
        assert response.json()["run_id"] == "review-redact-probe"


async def test_chat_debug_response_redacts_managed_document_reference(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
) -> None:
    """Normal and debug Chat responses redact managed document references."""
    harness = _install_chat_asset(
        asset_http_context,
        purpose="document",
        filename="debug.pdf",
        content=b"%PDF-1.4 debug\n",
    )
    reference = _document_reference(harness)
    captured: dict[str, Any] = {}
    install_chat_handler(
        asset_http_context.monkeypatch,
        captured,
        content=f"answer uses {reference}",
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.chat-debug-redact.test",
    ) as client:
        normal = await chat_completion(
            client,
            asset_http_context.api_key,
            content="summarize",
            attachments=[{"asset_id": harness.asset_id}],
        )
        debug = await chat_completion(
            client,
            asset_http_context.api_key,
            content="summarize",
            attachments=[{"asset_id": harness.asset_id}],
            debug=True,
        )
    for response in (normal, debug):
        assert response.status_code == 200, response.text
        assert reference not in response.text
        assert "<redacted-attachment>" in response.text
    record = RunRegistry(asset_http_context.db_path).list_runs(owner="u1")[0]
    dumped = json.dumps(record.result)
    assert reference not in dumped
