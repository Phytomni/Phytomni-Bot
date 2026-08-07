# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Compatibility contracts for the app-level native-run facade."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from tests.support.attachment_fakes import (
    managed_dataset_evidence_item,
)

from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.attachments import ManagedAttachmentEvidence

pytestmark = pytest.mark.server


def test_native_run_facade_preserves_signature_and_module_identity() -> None:
    """Keep the app-level native-run seam's callable metadata stable."""
    invoke = getattr(api_app_module, "_invoke_agent_run")
    signature = inspect.signature(invoke)
    assert tuple(signature.parameters) == (
        "agent",
        "arguments",
        "conversation_messages",
        "agent_thread_id",
        "private_agent_state",
        "dialogue_id",
        "request_json",
        "attachment_evidence",
        "debug",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.return_annotation == "tuple[dict[str, Any], int]"
    assert invoke.__module__ == api_app_module.__name__
    assert invoke.__qualname__ == "_invoke_agent_run"
    annotations = invoke.__annotations__
    expected_annotations = (
        ("agent", "str"),
        ("arguments", "dict[str, Any]"),
        ("conversation_messages", "tuple[dict[str, str], ...]"),
        ("agent_thread_id", "str | None"),
        ("private_agent_state", "Mapping[str, Any] | None"),
        ("dialogue_id", "str | None"),
        ("request_json", "str | None"),
        (
            "attachment_evidence",
            "ManagedAttachmentEvidence | None",
        ),
        ("debug", "bool"),
        ("return", "tuple[dict[str, Any], int]"),
    )
    assert tuple(annotations) == tuple(
        name for name, _value in expected_annotations
    )
    assert all(
        annotations[name] == value for name, value in expected_annotations
    )


def test_native_run_facade_binds_arguments_before_returning_coroutine() -> (
    None
):
    """Keep the old async facade's immediate argument errors."""
    invoke = getattr(api_app_module, "_invoke_agent_run")
    with pytest.raises(TypeError):
        invoke(arguments={})
    with pytest.raises(TypeError):
        invoke(agent="chat", arguments={}, unexpected=True)
    with pytest.raises(TypeError):
        getattr(invoke, "__call__")("chat", {})

    coroutine = invoke(agent="chat", arguments={})
    assert inspect.iscoroutine(coroutine)
    coroutine.close()


def test_native_run_preflight_uses_app_compatibility_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moved preflight keeps request-context and attachment seams patchable."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        api_app_module, "current_request_user", lambda: "compat-owner"
    )
    monkeypatch.setattr(
        api_app_module, "current_request_id", lambda: "compat-request"
    )
    monkeypatch.setattr(
        api_app_module,
        "resolve_tasks_db_path",
        lambda: "compat-db",
    )

    def capture_attachments(*args: Any, **kwargs: Any) -> None:
        """Capture the compatibility seam's owner and database inputs."""
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        api_app_module,
        "validate_native_attachments",
        capture_attachments,
    )
    preflight = getattr(api_app_module, "_preflight_agent_run")(
        agent="chat",
        arguments={"user_query": "compat query"},
        dialogue_id="compat-dialogue",
        request_json=None,
        attachment_evidence=None,
    )

    assert preflight.owner == "compat-owner"
    assert preflight.request_info.request_id == "compat-request"
    assert captured["args"] == ("chat", {"user_query": "compat query"})
    assert captured["kwargs"] == {
        "owner": "compat-owner",
        "db_path": "compat-db",
        "managed_evidence": None,
    }


def test_native_run_preflight_keeps_attachment_owner_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence owner validates attachments without changing run owner."""
    captured: dict[str, Any] = {}
    evidence = ManagedAttachmentEvidence(
        attachment_owner="delegated-owner",
        items=(
            managed_dataset_evidence_item(
                asset_id="file_dataset",
                reference="obs://dataset",
            ),
        ),
    )
    monkeypatch.setattr(
        api_app_module, "current_request_user", lambda: "run-owner"
    )
    monkeypatch.setattr(
        api_app_module, "current_request_id", lambda: "compat-request"
    )
    monkeypatch.setattr(
        api_app_module,
        "resolve_tasks_db_path",
        lambda: "compat-db",
    )

    def capture_attachments(*args: Any, **kwargs: Any) -> None:
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        api_app_module,
        "validate_native_attachments",
        capture_attachments,
    )
    preflight = getattr(api_app_module, "_preflight_agent_run")(
        agent="analyst",
        arguments={
            "goal_description": "compat",
            "data_list": {"obs://dataset": ""},
            "obs_file_list": [],
        },
        dialogue_id=None,
        request_json=None,
        attachment_evidence=evidence,
    )

    assert preflight.owner == "run-owner"
    assert captured["kwargs"] == {
        "owner": "delegated-owner",
        "db_path": "compat-db",
        "managed_evidence": evidence,
    }
