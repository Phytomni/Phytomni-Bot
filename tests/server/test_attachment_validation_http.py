# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP-boundary attachment capability and ownership contracts."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from tests.support.http_fakes import (
    assert_duplicate_attachment_response,
    install_rejection_handler,
)
from tests.support.resolver_fakes import (
    NativeRunHandlerSpec,
    post_duplicate_attachment_run,
    post_native_run_with_handler,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.api.agent_capabilities import (
    serialize_agent_capability,
)
from mcp_server_phytomni.api.attachments import (
    AttachmentContractError,
    validate_agent_attachments,
)
from mcp_server_phytomni.config.defaults import ApiConfig, ServerConfig
from mcp_server_phytomni.runtime.upload_registry import (
    UploadMetadata,
    UploadRegistry,
)
from mcp_server_phytomni.storage.obs_storage import obs_path_from_key

pytestmark = pytest.mark.server


def register_fixture_upload(
    registry: UploadRegistry,
    *,
    owner: str,
    purpose: str,
    filename: str = "context.pdf",
    byte_size: int = 1_024,
) -> str:
    """Persist one trusted fixture upload and return its public OBS path."""
    file_id = f"fixture-{uuid4().hex}"
    extension = filename.rsplit(".", maxsplit=1)[-1].lower()
    upload_format = extension
    prefix = ApiConfig().API_UPLOAD_PREFIX.strip("/")
    key = f"{prefix}/{owner}/fixture/{file_id}/{filename}"
    path = obs_path_from_key(ServerConfig().BUCKET_NAME, key)
    registry.record(
        UploadMetadata(
            file_id=file_id,
            user_id=owner,
            obs_path=path,
            filename=filename,
            purpose=purpose,
            byte_size=byte_size,
            format=upload_format,
            media_type="application/octet-stream",
            created_at="2026-07-25T00:00:00+00:00",
        )
    )
    return path


def assert_attachment_error(
    registry: UploadRegistry,
    *,
    agent: str,
    arguments: dict[str, Any],
    code: str,
    owner: str = "u1",
) -> None:
    """Assert one stable contract code without exposing attachment paths."""
    with pytest.raises(AttachmentContractError) as raised:
        validate_agent_attachments(
            agent,
            arguments,
            owner=owner,
            registry=registry,
        )
    assert raised.value.code == code
    assert all(path not in str(raised.value) for path in arguments)


@pytest.mark.parametrize(
    ("agent", "channel", "allowed"),
    [
        ("chat", "documents", True),
        ("knowledge", "documents", True),
        ("review", "documents", True),
        ("analyst", "documents", True),
        ("analyst", "datasets", True),
        ("research", "documents", True),
        ("research", "datasets", True),
        ("data", "documents", False),
        ("data", "datasets", False),
        ("brief_gene", "documents", False),
        ("deep_genome", "documents", False),
        ("design", "documents", True),
        ("design", "datasets", False),
        ("network", "documents", True),
        ("network", "datasets", False),
    ],
)
def test_native_attachment_matrix(
    tasks_db_path: str,
    agent: str,
    channel: str,
    allowed: bool,
) -> None:
    """The capability registry is enforced for every native agent slug."""
    registry = UploadRegistry(tasks_db_path)
    is_dataset = channel == "datasets"
    path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="dataset" if is_dataset else "agent_context",
        filename="input.csv" if is_dataset else "context.pdf",
    )
    arguments = (
        {"data_list": {path: "CSV input"}}
        if is_dataset
        else {"obs_file_list": [path]}
    )

    if allowed:
        selection = validate_agent_attachments(
            agent,
            arguments,
            owner="u1",
            registry=registry,
        )
        assert selection.datasets if is_dataset else selection.documents
    else:
        assert_attachment_error(
            registry,
            agent=agent,
            arguments=arguments,
            code="attachment_not_supported",
        )


def test_validator_limits_match_public_attachment_contract() -> None:
    """The validator and public capability descriptor share exact limits."""
    for slug, channel in (
        ("chat", "document_context"),
        ("analyst", "datasets"),
        ("research", "datasets"),
    ):
        limits = serialize_agent_capability(slug)["attachments"][channel]
        assert limits["max_file_bytes"] == 26_214_400
        assert limits["max_files"] == 10
        assert limits["max_total_bytes"] == 52_428_800


def test_duplicate_paths_are_rejected_across_channels(
    tasks_db_path: str,
) -> None:
    """Repeated paths are a contract error and are never silently deduped."""
    registry = UploadRegistry(tasks_db_path)
    path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="agent_context",
    )
    assert_attachment_error(
        registry,
        agent="analyst",
        arguments={
            "obs_file_list": [path],
            "data_list": {path: "same object"},
        },
        code="attachment_duplicate",
    )
    assert_attachment_error(
        registry,
        agent="chat",
        arguments={"obs_file_list": [path, path]},
        code="attachment_duplicate",
    )


def test_owner_purpose_format_and_description_fail_closed(
    tasks_db_path: str,
) -> None:
    """Untrusted ownership and metadata cannot authorize an attachment."""
    registry = UploadRegistry(tasks_db_path)
    foreign_path = register_fixture_upload(
        registry,
        owner="u2",
        purpose="agent_context",
    )
    assert_attachment_error(
        registry,
        agent="chat",
        arguments={"obs_file_list": [foreign_path]},
        code="attachment_not_found",
    )

    wrong_purpose = register_fixture_upload(
        registry,
        owner="u1",
        purpose="dataset",
        filename="context.pdf",
    )
    assert_attachment_error(
        registry,
        agent="chat",
        arguments={"obs_file_list": [wrong_purpose]},
        code="attachment_purpose_mismatch",
    )

    wrong_format = register_fixture_upload(
        registry,
        owner="u1",
        purpose="agent_context",
        filename="context.txt",
    )
    assert_attachment_error(
        registry,
        agent="chat",
        arguments={"obs_file_list": [wrong_format]},
        code="attachment_format_unsupported",
    )

    dataset_path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="dataset",
        filename="input.csv",
    )
    assert_attachment_error(
        registry,
        agent="analyst",
        arguments={"data_list": {dataset_path: "  "}},
        code="attachment_description_required",
    )


def test_legacy_dataset_paths_remain_outside_user_upload_budget(
    tasks_db_path: str,
) -> None:
    """Existing preconfigured OBS datasets remain a separate trust channel."""
    selection = validate_agent_attachments(
        "research",
        {"data_list": {"/obs/phytomni/prepared/input.fasta": "reference"}},
        owner="u1",
        registry=UploadRegistry(tasks_db_path),
    )
    assert not selection.datasets
    assert selection.legacy_dataset_paths == (
        "/obs/phytomni/prepared/input.fasta",
    )


def test_managed_upload_without_owner_metadata_is_not_legacy(
    tasks_db_path: str,
) -> None:
    """A path in the managed prefix cannot fall back to legacy trust."""
    path = "/obs/phytomni/agent_data/uploads/u1/fixture/missing/input.csv"
    assert_attachment_error(
        UploadRegistry(tasks_db_path),
        agent="analyst",
        arguments={"data_list": {path: "input"}},
        code="attachment_not_found",
    )


@pytest.mark.parametrize(
    ("byte_size", "expected"),
    [(26_214_400, None), (26_214_401, "attachment_limit_exceeded")],
)
def test_per_file_limit_is_inclusive(
    tasks_db_path: str,
    byte_size: int,
    expected: str | None,
) -> None:
    """The exact 25 MiB boundary passes and the next byte fails."""
    registry = UploadRegistry(tasks_db_path)
    path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="agent_context",
        byte_size=byte_size,
    )
    arguments = {"obs_file_list": [path]}
    if expected is None:
        validate_agent_attachments(
            "chat", arguments, owner="u1", registry=registry
        )
    else:
        assert_attachment_error(
            registry,
            agent="chat",
            arguments=arguments,
            code=expected,
        )


@pytest.mark.parametrize(
    ("count", "expected"),
    [(10, None), (11, "attachment_limit_exceeded")],
)
def test_file_count_limit_is_inclusive(
    tasks_db_path: str,
    count: int,
    expected: str | None,
) -> None:
    """The tenth upload passes and the eleventh upload fails."""
    registry = UploadRegistry(tasks_db_path)
    paths = [
        register_fixture_upload(
            registry,
            owner="u1",
            purpose="agent_context",
            filename=f"context-{index}.pdf",
        )
        for index in range(count)
    ]
    arguments = {"obs_file_list": paths}
    if expected is None:
        validate_agent_attachments(
            "chat", arguments, owner="u1", registry=registry
        )
    else:
        assert_attachment_error(
            registry,
            agent="chat",
            arguments=arguments,
            code=expected,
        )


def test_total_file_limit_is_inclusive(tasks_db_path: str) -> None:
    """The aggregate boundary is shared across attachment channels."""
    registry = UploadRegistry(tasks_db_path)
    exact_paths = [
        register_fixture_upload(
            registry,
            owner="u1",
            purpose="agent_context",
            filename=f"context-{index}.pdf",
            byte_size=26_214_400,
        )
        for index in range(2)
    ]
    validate_agent_attachments(
        "chat",
        {"obs_file_list": exact_paths},
        owner="u1",
        registry=registry,
    )

    extra_path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="agent_context",
        filename="context-extra.pdf",
        byte_size=1,
    )
    assert_attachment_error(
        registry,
        agent="chat",
        arguments={"obs_file_list": [*exact_paths, extra_path]},
        code="attachment_limit_exceeded",
    )


@pytest.mark.parametrize("agent", ["design", "network"])
def test_unregistered_design_and_network_paths_are_rejected(
    tasks_db_path: str,
    agent: str,
) -> None:
    """Raw paths reach owner validation but remain unregistered."""
    assert_attachment_error(
        UploadRegistry(tasks_db_path),
        agent=agent,
        arguments={"obs_file_list": ["/obs/phytomni/legacy.pdf"]},
        code="attachment_not_found",
    )


async def test_http_native_run_validates_registered_upload_before_handler(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """A trusted document reaches ChatAgent after invocation validation."""
    registry = UploadRegistry(tasks_db_path)
    path = register_fixture_upload(
        registry,
        owner="u1",
        purpose="agent_context",
    )
    invoked = False

    async def fake(args: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked = True
        assert args.obs_file_list == [path]
        return {"answer": "ok", "doc_list": []}

    response = await post_native_run_with_handler(
        monkeypatch,
        api_client,
        issued_api_key,
        NativeRunHandlerSpec(
            tool_name=server.PhytomniAgents.CHAT_AGENT.value,
            handler=fake,
            agent_slug="chat",
            arguments={"user_query": "hi", "obs_file_list": [path]},
        ),
    )
    assert response.status_code == 200
    assert invoked


async def test_http_attachment_error_has_stable_projection(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate paths stop the handler and use the unified 422 error body."""
    marker = install_rejection_handler(
        monkeypatch, server.PhytomniAgents.CHAT_AGENT.value
    )
    path = "/obs/phytomni/agent_data/uploads/u1/fixture/missing.pdf"
    response = await post_duplicate_attachment_run(
        api_client, issued_api_key, path
    )
    assert_duplicate_attachment_response(response)
    error = response.json()["error"]
    assert error["code"] == "attachment_duplicate"
    assert error["stage"] == "attachment_validation"
    assert error["retryable"] is False
    assert path not in response.text
    assert not marker["called"]
