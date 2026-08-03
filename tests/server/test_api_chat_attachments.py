# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat document-only attachment resolution and response redaction tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.support.chat_fakes import install_chat_handler
from tests.support.handler_fakes import review_success_result
from tests.support.http_fakes import (
    build_instant_chat_context_envelope,
    open_asgi_client,
)
from tests.support.resumable_asset_fakes import (
    AssetHttpTestContext,
    ResumableAssetSpec,
    build_resumable_asset,
    enable_conversation_context_v1,
)

import mcp_server_phytomni.agents.chat.service as chat_service
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.conversation_context.store import (
    ConversationContextStore,
)
from mcp_server_phytomni.runtime.resumable_uploads import UploadAssetPurpose
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


def _context_turn_counts(db_path: Path) -> tuple[int, int]:
    """Return conversation context and turn row counts for one task DB."""
    with sqlite3.connect(str(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "conversation_contexts" not in tables:
            return 0, 0
        contexts = connection.execute(
            "SELECT COUNT(*) FROM conversation_contexts"
        ).fetchone()[0]
        turns = connection.execute(
            "SELECT COUNT(*) FROM conversation_turns"
        ).fetchone()[0]
    return int(contexts), int(turns)


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
    reference = _document_reference(harness)
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


async def test_chat_dataset_asset_returns_attachment_not_supported(
    asset_http_context: AssetHttpTestContext,
    chat_completion: Callable[..., Any],
) -> None:
    """Dataset assets are rejected before Chat invocation or staging."""
    harness = _install_chat_asset(
        asset_http_context,
        purpose="dataset",
        filename="chat.csv",
        content=b"a,b\n1,2\n",
    )
    called = {"chat": 0}

    async def forbid_chat(**_kwargs: Any) -> dict[str, Any]:
        called["chat"] += 1
        raise AssertionError("phyto_chat must not run")

    asset_http_context.monkeypatch.setattr(
        chat_service, "phyto_chat", forbid_chat
    )
    app = api_app_module.create_app()
    async with open_asgi_client(
        asset_http_context.monkeypatch,
        app,
        base_url="http://api.chat-dataset.test",
    ) as client:
        response = await chat_completion(
            client,
            asset_http_context.api_key,
            content="analyze this csv",
            attachments=[{"asset_id": harness.asset_id}],
            dataset_description="should not bypass purpose",
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert called["chat"] == 0
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


async def test_chat_context_dataset_asset_returns_attachment_not_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    chat_completion: Callable[..., Any],
) -> None:
    """Instant Chat context rejects datasets before staging or Chat invoke."""
    context, key = enable_conversation_context_v1(monkeypatch, tmp_path)
    harness = _install_chat_asset(
        context,
        purpose="dataset",
        filename="ctx.csv",
        content=b"a,b\n1,2\n",
    )
    called = {"chat": 0, "begin_turn": 0}
    original_begin = ConversationContextStore.begin_turn

    async def forbid_chat(**_kwargs: Any) -> dict[str, Any]:
        called["chat"] += 1
        raise AssertionError("phyto_chat must not run")

    def forbid_begin_turn(
        self: ConversationContextStore,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        called["begin_turn"] += 1
        return original_begin(self, *args, **kwargs)

    monkeypatch.setattr(chat_service, "phyto_chat", forbid_chat)
    monkeypatch.setattr(
        ConversationContextStore, "begin_turn", forbid_begin_turn
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
            dataset_description="should not bypass purpose",
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert called["chat"] == 0
    assert called["begin_turn"] == 0
    tasks_db = tmp_path / "tasks.sqlite"
    assert _context_turn_counts(tasks_db) == (0, 0)
    assert not RunRegistry(str(tasks_db)).list_runs(owner="u1")


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
