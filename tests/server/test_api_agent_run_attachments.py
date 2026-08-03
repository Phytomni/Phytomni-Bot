# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Native agent-run attachment projection and redaction tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from tests.server.test_api_agent_runs import _BACKGROUND_CASES, _RemoteCase
from tests.support.http_fakes import (
    install_rejection_handler,
    install_tool_handler,
    open_asgi_client,
)
from tests.support.resumable_asset_fakes import (
    AssetHttpTestContext,
    AssetRejectionCase,
    BackgroundAssetCase,
    ResumableAssetSpec,
    build_resumable_asset,
    execute_opaque_asset_run,
    execute_rejected_asset_run,
    install_attachment_capture,
    wait_for_attachment_submission,
)

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.shared.dataset_description import (
    DatasetDescriptionResult,
)
from mcp_server_phytomni.api import app as api_app_module
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.lifecycle_contract import empty_agent_result
from mcp_server_phytomni.api.routes import (
    attachment_inputs as attachment_inputs_module,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.runtime.run_registry import RunRegistry

pytestmark = pytest.mark.server


@pytest.fixture(name="asset_http_context")
def _asset_http_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tasks_db_path: str,
    issued_api_key: str,
) -> AssetHttpTestContext:
    """Bundle opaque-asset HTTP fixtures without hiding their ownership."""
    return AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=tasks_db_path,
        api_key=issued_api_key,
    )


def _auth(key: str) -> dict[str, str]:
    """Build one Bearer auth header."""
    return {"Authorization": f"Bearer {key}"}


def _create_scoped_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Any, dict[str, str]]:
    """Create an isolated app plus the three delegated-owner key shapes."""
    monkeypatch.setenv("PHYTOMNI_TASKS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(tmp_path / "keys.sqlite"))
    store = ApiKeyStore(str(tmp_path / "keys.sqlite"))
    keys = {
        "scope_less": store.create(user_id="principal-owner").api_key,
        "agents": store.create(
            user_id="principal-owner", scopes=["agents"]
        ).api_key,
        "delegate": store.create(
            user_id="web-service", scopes=["agents", "files:delegate"]
        ).api_key,
    }
    return api_app_module.create_app(), keys


def _dataset_arguments(slug: str, query: str) -> dict[str, Any]:
    """Return schema-shaped Analyst or Research dataset arguments."""
    if slug == "analyst":
        return {
            "goal_description": query,
            "data_list": {},
            "obs_file_list": [],
        }
    return {"user_query": query, "data_list": {}, "obs_file_list": []}


def _install_dataset_assets(
    context: AssetHttpTestContext,
    *,
    owner: str = "u1",
    dataset_filename: str = "input.csv",
) -> tuple[Any, str, str]:
    """Create one completed dataset and document and return references."""
    dataset = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename=dataset_filename,
            content=b"gene,value\nAT1G01010,7\n",
            purpose="dataset",
        ),
    )
    document = build_resumable_asset(
        context.tmp_path,
        db_path=context.db_path,
        spec=ResumableAssetSpec(
            owner=owner,
            filename="context.pdf",
            content=b"%PDF-1.4\ncontext\n",
            purpose="chat_attachment",
        ),
    )
    context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: dataset.resolver,
    )
    return dataset.resolver, dataset.asset_id, document.asset_id


async def _post_asset_run(
    context: AssetHttpTestContext,
    **options: Any,
) -> httpx.Response:
    """POST one native run with opaque attachments."""
    app = api_app_module.create_app()
    payload: dict[str, Any] = {
        "arguments": options["arguments"],
        "attachments": options["attachments"],
    }
    for key in ("owner_subject", "dataset_description", "debug"):
        if options.get(key) is not None:
            payload[key] = options[key]
    async with open_asgi_client(
        context.monkeypatch,
        app,
        base_url="http://api.asset.test",
    ) as client:
        return await client.post(
            f"/v1/agents/{options['slug']}/runs",
            headers=_auth(options.get("api_key") or context.api_key),
            json=payload,
        )


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
async def test_native_background_run_preserves_empty_obs_file_list(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: BackgroundAssetCase,
) -> None:
    """An explicit empty attachment list reaches all four typed Agents."""
    captured: dict[str, Any] = {}
    install_attachment_capture(monkeypatch, case, captured)
    endpoint = f"/v1/agents/{case.slug}/runs"
    payload = {"arguments": case.arguments, "attachments": []}

    response = await api_client.post(
        endpoint,
        headers=_auth(issued_api_key),
        json=payload,
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=tasks_db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert arguments.obs_file_list == []


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
async def test_native_background_run_resolves_opaque_owner_asset(
    asset_http_context: AssetHttpTestContext,
    case: BackgroundAssetCase,
) -> None:
    """An owner-completed opaque asset becomes one internal reference."""
    harness, response, arguments = await execute_opaque_asset_run(
        asset_http_context,
        case,
    )
    assert response.status_code == 202
    assert len(arguments.obs_file_list) == 1
    internal_reference = arguments.obs_file_list[0]
    assert not hasattr(arguments, "attachments")
    assert "attachments" not in arguments.model_dump()
    assert harness.asset_id not in response.text
    assert internal_reference not in response.text


async def test_owner_subject_is_authorized_as_attachment_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit owner assertions require files:delegate even for self."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)

    async def fail_invoke(**_kwargs: Any) -> tuple[dict[str, Any], int]:
        raise AssertionError("validation should fail before invocation")

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fail_invoke)
    request = {
        "arguments": {
            "goal_description": "bounded analysis",
            "data_list": {},
            "obs_file_list": [],
        },
        "owner_subject": "principal-owner",
    }
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.owner.test"
    ) as client:
        no_scope = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["scope_less"]),
            json=request,
        )
        agents_only = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["agents"]),
            json=request,
        )
        blank = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["delegate"]),
            json={**request, "owner_subject": "   "},
        )

    for response in (no_scope, agents_only):
        assert response.status_code == 403
        assert (
            response.json()["error"]["message"] == "request is not permitted"
        )
        assert "principal-owner" not in response.text
        assert "files:delegate" not in response.text
    assert blank.status_code == 422
    assert "validation should fail" not in blank.text


async def test_omitted_owner_subject_preserves_scope_less_principal_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Absent owner_subject keeps the authenticated principal behavior."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_invoke(**kwargs: Any) -> tuple[dict[str, Any], int]:
        calls.append(kwargs)
        return (
            {
                "id": "principal-run",
                "run_id": "principal-run",
                "object": "agent.run",
                "agent": "analyst",
                "status": "running",
                "task_ids": [],
                "result": empty_agent_result(),
            },
            202,
        )

    monkeypatch.setattr(api_app_module, "_invoke_agent_run", fake_invoke)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.owner.test"
    ) as client:
        response = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["scope_less"]),
            json={
                "arguments": {
                    "goal_description": "bounded analysis",
                    "data_list": {},
                    "obs_file_list": [],
                }
            },
        )

    assert response.status_code == 202
    assert calls[0]["attachment_evidence"] is None
    assert not RunRegistry(str(tmp_path / "tasks.sqlite")).list_runs(
        owner="principal-owner"
    )


@pytest.mark.parametrize(
    ("slug", "query_key"),
    [("analyst", "goal_description"), ("research", "user_query")],
)
async def test_direct_dataset_assets_project_to_data_list_before_202(
    asset_http_context: AssetHttpTestContext,
    slug: str,
    query_key: str,
) -> None:
    """Managed datasets are described and submitted before 202 acceptance."""
    _resolver, dataset_id, document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug=slug,
        tool_name=(
            server.PhytomniAgents.ANALYST_AGENT.value
            if slug == "analyst"
            else server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value
        ),
        stub_return=(
            {"task_id": f"T-{slug}", "output_dir": "/obs/out"}
            if slug == "analyst"
            else {"task_ids": [f"T-{slug}"], "output_dir": "/obs/out"}
        ),
        arguments=_dataset_arguments(slug, f"{slug} query"),
        expected_task_ids={f"T-{slug}"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("supplied description should skip provider")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug=slug,
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}, {"asset_id": document_id}],
        dataset_description="supplied batch description",
    )

    assert response.status_code == 202, response.text
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    dumped = arguments.model_dump()
    dataset_reference = next(iter(dumped["data_list"]))
    assert dumped[query_key] == f"{slug} query"
    assert dumped["data_list"] == {
        dataset_reference: "supplied batch description"
    }
    assert len(dumped["obs_file_list"]) == 1
    assert "attachments" not in dumped
    assert "owner_subject" not in dumped
    assert "dataset_description" not in dumped


async def test_direct_dataset_blank_description_uses_one_completion_result(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """A blank batch description is completed once for managed datasets."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-generated", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "complete this dataset"),
        expected_task_ids={"T-generated"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)
    calls: list[dict[str, Any]] = []

    async def fake_completion(**kwargs: Any) -> DatasetDescriptionResult:
        calls.append(kwargs)
        return DatasetDescriptionResult(("generated role",), "generated")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fake_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="analyst",
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}],
        dataset_description=" ",
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert calls[0]["query"] == "complete this dataset"
    assert list(arguments.data_list.values()) == ["generated role"]


async def test_dataset_assets_fail_before_completion_and_reservation(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Unsupported dataset assets do not complete, persist, or launch."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    marker = install_rejection_handler(
        asset_http_context.monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
    )
    background_launcher = Mock(name="background_launcher")
    asset_http_context.monkeypatch.setattr(
        api_app_module, "launch_background_submission", background_launcher
    )

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("unsupported agent should not complete")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": "hi", "obs_file_list": []},
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "attachment_not_supported"
    assert marker["called"] is False
    assert background_launcher.call_count == 0
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


async def test_dataset_completion_blocks_before_umbrella_reservation(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """The POST cannot return 202 while dataset completion is pending."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        started.set()
        await release.wait()
        return DatasetDescriptionResult(("ready",), "generated")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        slow_completion,
        raising=False,
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-blocked", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "wait for data"),
        expected_task_ids={"T-blocked"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)
    request_task = asyncio.create_task(
        _post_asset_run(
            asset_http_context,
            slug="analyst",
            arguments=case.arguments,
            attachments=[{"asset_id": dataset_id}],
        )
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(request_task), timeout=0.01)
    release.set()
    response = await request_task

    assert response.status_code == 202


async def test_managed_tsv_dataset_asset_fails_before_completion(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Managed dataset format validation runs before description completion."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context, dataset_filename="input.tsv"
    )

    async def fail_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        raise AssertionError("unsupported format should not complete")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        fail_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="analyst",
        arguments=_dataset_arguments("analyst", "tsv dataset"),
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_asset_format"
    assert not RunRegistry(asset_http_context.db_path).list_runs(owner="u1")


async def test_empty_dataset_completion_still_submits_managed_blank(
    asset_http_context: AssetHttpTestContext,
) -> None:
    """Managed evidence permits an empty generated description to submit."""
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        asset_http_context
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="research",
        tool_name=server.PhytomniAgents.IN_SILICO_RESEARCH_AGENT.value,
        stub_return={"task_ids": ["T-empty"], "output_dir": "/obs/out"},
        arguments=_dataset_arguments("research", "empty completion"),
        expected_task_ids={"T-empty"},
    )
    install_attachment_capture(asset_http_context.monkeypatch, case, captured)

    async def empty_completion(**_kwargs: Any) -> DatasetDescriptionResult:
        return DatasetDescriptionResult(("",), "empty")

    asset_http_context.monkeypatch.setattr(
        attachment_inputs_module,
        "complete_dataset_descriptions",
        empty_completion,
        raising=False,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="research",
        arguments=case.arguments,
        attachments=[{"asset_id": dataset_id}],
    )

    assert response.status_code == 202
    arguments = await wait_for_attachment_submission(
        captured=captured,
        db_path=asset_http_context.db_path,
        run_id=response.json()["run_id"],
        case=case,
    )
    assert list(arguments.data_list.values()) == [""]


async def test_delegated_asset_lookup_keeps_run_owner_as_principal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Delegation changes lookup owner but not accepted run ownership."""
    app, keys = _create_scoped_app(monkeypatch, tmp_path)
    context = AssetHttpTestContext(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        db_path=str(tmp_path / "tasks.sqlite"),
        api_key=keys["delegate"],
    )
    _resolver, dataset_id, _document_id = _install_dataset_assets(
        context, owner="principal-owner"
    )
    captured: dict[str, Any] = {}
    case = _RemoteCase(
        slug="analyst",
        tool_name=server.PhytomniAgents.ANALYST_AGENT.value,
        stub_return={"task_id": "T-delegated", "output_dir": "/obs/out"},
        arguments=_dataset_arguments("analyst", "delegated analysis"),
        expected_task_ids={"T-delegated"},
    )
    install_attachment_capture(monkeypatch, case, captured)
    async with open_asgi_client(
        monkeypatch, app, base_url="http://api.delegate.test"
    ) as client:
        response = await client.post(
            "/v1/agents/analyst/runs",
            headers=_auth(keys["delegate"]),
            json={
                "arguments": case.arguments,
                "attachments": [{"asset_id": dataset_id}],
                "owner_subject": "principal-owner",
                "dataset_description": "delegated dataset",
            },
        )

    assert response.status_code == 202, response.text
    registry = RunRegistry(context.db_path)
    arguments = None
    for _ in range(100):
        arguments = captured.get("arguments")
        record = registry.get_run(
            response.json()["run_id"], owner="web-service"
        )
        if (
            arguments is not None
            and record is not None
            and set(record.task_ids) == case.expected_task_ids
        ):
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("delegated background submission did not settle")
    assert list(arguments.data_list.values()) == ["delegated dataset"]
    assert registry.get_run(response.json()["run_id"], owner="web-service")
    assert (
        registry.get_run(response.json()["run_id"], owner="principal-owner")
        is None
    )


async def test_sync_document_asset_request_and_result_are_redacted(
    asset_http_context: AssetHttpTestContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Managed references stay out of request JSON, responses, and logs."""
    prompt = "canonical redaction prompt"
    provider_output = "provider-private-output"
    attachment_marker = "document-private-reference"
    harness = build_resumable_asset(
        asset_http_context.tmp_path,
        db_path=asset_http_context.db_path,
        spec=ResumableAssetSpec(
            owner="u1",
            filename="redaction.pdf",
            content=b"%PDF-1.4 redaction",
            purpose="chat_attachment",
        ),
    )
    asset_http_context.monkeypatch.setattr(
        UploadRuntime,
        "get_asset_resolver",
        lambda _runtime: harness.resolver,
    )

    async def fake(args: Any) -> dict[str, Any]:
        reference = args.obs_file_list[0]
        return {
            "choices": [{"message": {"content": f"safe answer {reference}"}}],
            "provider_output": provider_output,
            "attachment_marker": attachment_marker,
            "private_reference": reference,
        }

    install_tool_handler(
        asset_http_context.monkeypatch,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )
    response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": prompt, "obs_file_list": []},
        attachments=[{"asset_id": harness.asset_id}],
    )
    debug_response = await _post_asset_run(
        asset_http_context,
        slug="chat",
        arguments={"user_query": prompt, "obs_file_list": []},
        attachments=[{"asset_id": harness.asset_id}],
        debug=True,
    )

    assert response.status_code == 200
    assert debug_response.status_code == 200
    listing = RunRegistry(asset_http_context.db_path).list_runs(owner="u1")
    assert len(listing) == 2
    info = listing[0].request_info
    assert info.query == prompt
    assert json.loads(info.request_json or "{}") == {
        "dialogue_id": None,
        "locale": "en-US",
        "route": "chat",
    }
    rendered = (
        response.text
        + debug_response.text
        + json.dumps(listing[0].result or {})
        + caplog.text
    )
    for sentinel in (
        harness.asset_id,
        provider_output,
        attachment_marker,
        "redaction.pdf",
    ):
        assert sentinel not in rendered
        assert sentinel not in (info.request_json or "")
    assert prompt not in (info.request_json or "")


_ATTACHMENT_REJECTION_CASES = (
    pytest.param(
        AssetRejectionCase("foreign", 404, "upload_asset_not_found"),
        id="foreign",
    ),
    pytest.param(
        AssetRejectionCase("incomplete", 409, "upload_state_conflict"),
        id="incomplete",
    ),
    pytest.param(
        AssetRejectionCase("duplicate", 409, "upload_state_conflict"),
        id="duplicate",
    ),
    pytest.param(
        AssetRejectionCase("malformed", 422, "invalid_upload_metadata"),
        id="malformed",
    ),
)


@pytest.mark.parametrize("case", _BACKGROUND_CASES)
@pytest.mark.parametrize("rejection", _ATTACHMENT_REJECTION_CASES)
async def test_native_background_run_rejects_unsafe_opaque_asset(
    asset_http_context: AssetHttpTestContext,
    caplog: pytest.LogCaptureFixture,
    case: BackgroundAssetCase,
    rejection: AssetRejectionCase,
) -> None:
    """Owner, completion, uniqueness, and format failures stay private."""
    response, private_values, handler_called = (
        await execute_rejected_asset_run(
            asset_http_context,
            case,
            rejection.scenario,
        )
    )
    assert response.status_code == rejection.status_code
    assert response.json()["error"]["code"] == rejection.safe_code
    assert handler_called is False
    rendered = f"{response.text}\n{caplog.text}"
    for private_value in private_values:
        assert private_value not in rendered
