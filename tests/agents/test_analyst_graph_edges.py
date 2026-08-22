# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for analyst/graph.py submit helpers and grant wrapping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from mcp.shared.exceptions import McpError

import mcp_server_phytomni.agents.analyst.graph as analyst_graph
from mcp_server_phytomni.agents.analyst.graph import (
    AnalystGraphMixin,
    CustomDumper,
    LiteralString,
    _relay_analysis_body,
)
from mcp_server_phytomni.agents.analyst.state import AnalystState
from mcp_server_phytomni.storage.path_policy import RunIdentity

pytestmark = pytest.mark.agent


def _host(**config: Any) -> SimpleNamespace:
    """Return a duck-typed mixin host with the given config fields."""
    return SimpleNamespace(analyst_config=SimpleNamespace(**config))


def _identity() -> RunIdentity:
    """Return a throwaway run identity for submit helpers."""
    return RunIdentity.create(user_id="graph-edges", scope="analysis")


def test_submit_run_identity_uses_config_user() -> None:
    """The submit identity is minted from the configured USER_ID."""
    host = _host(USER_ID="alice")
    identity = getattr(AnalystGraphMixin, "_submit_run_identity")(host)

    assert identity.user_id == "alice"
    assert "analysis_agents_task" in identity.run_id


async def test_submit_output_dir_returns_flagged_child() -> None:
    """A flagged result child is reused without creating a new directory."""
    host = _host(CREATE_DIR=True)
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")
    output_dir = await submit_output_dir(
        host,
        {
            "output_dir": "/obs/run/children/part-003",
            "output_dir_is_result_child": True,
        },
        _identity(),
    )

    assert output_dir == "/obs/run/children/part-003"


async def test_submit_output_dir_reallocates_flagged_child_under_shared_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flagged child under the config dump is not treated as isolated."""
    captured: dict[str, Any] = {}

    async def fake_ensure(*_args: Any, **kwargs: Any) -> str:
        captured["run_root"] = (
            _args[3] if len(_args) > 3 else kwargs.get("output_dir")
        )
        return "/obs/scoped"

    monkeypatch.setattr(analyst_graph, "ensure_run_output_dir", fake_ensure)
    default = "/obs/phytomni/agent_data/test/output"
    host = _host(CREATE_DIR=True, OUTPUT_DIR=default)
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")

    output_dir = await submit_output_dir(
        host,
        {
            "output_dir": f"{default}/children/part-001",
            "output_dir_is_result_child": True,
            "input_fingerprint": "",
        },
        _identity(),
    )

    assert captured["run_root"] == ""
    assert output_dir == "/obs/scoped/children/part-001"
    assert not output_dir.startswith(default)


def test_submit_job_data_uses_app_id_outside_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct mode binds tool_id from APP_ID; relay binds compute_resource."""
    host = _host(
        TASK_NAME="analyst_agents_task",
        COMPUTE_RESOURCE="small",
        RESOURCE={"small": {"cpu": 1, "memory": 4}},
        APP_ID={"small": "app-small"},
        ANALYSIS_JOB_TIMEOUT=99,
    )
    submit_job_data = getattr(AnalystGraphMixin, "_submit_job_data")
    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: False)
    _name, payload = submit_job_data(
        host, {}, "/obs/task.yaml", "/obs/model.yaml"
    )
    assert payload["tool_id"] == "app-small"
    assert payload["timeout"] == 99

    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: True)
    _name, relay_payload = submit_job_data(
        host, {}, "/obs/task.yaml", "/obs/model.yaml"
    )
    assert relay_payload["compute_resource"] == "small"
    assert "tool_id" not in relay_payload


def test_relay_analysis_body_returns_plain_job_without_sidecar() -> None:
    """No sidecar keeps the historical analysis request bytes."""
    job = {"name": "plain"}
    assert _relay_analysis_body(job, None) is job


def test_literal_string_renders_as_block_scalar() -> None:
    """CustomDumper emits LiteralString values in YAML ``|`` style."""
    dumped = yaml.dump(
        {"goal": LiteralString("line one\nline two\n")},
        Dumper=CustomDumper,
        default_flow_style=False,
    )

    assert dumped.startswith("goal: |")
    assert "line one" in dumped


async def test_submit_output_dir_returns_existing_when_create_disabled() -> (
    None
):
    """CREATE_DIR=False keeps the caller-supplied directory verbatim."""
    host = _host(CREATE_DIR=False)
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")
    output_dir = await submit_output_dir(
        host,
        {"output_dir": "/obs/keep-me"},
        _identity(),
    )

    assert output_dir == "/obs/keep-me"


async def test_submit_output_dir_clears_shared_default_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The configured test dump is not treated as a caller-owned root."""
    captured: dict[str, Any] = {}

    async def fake_ensure(*_args: Any, **kwargs: Any) -> str:
        captured["run_root"] = (
            _args[3] if len(_args) > 3 else kwargs.get("output_dir")
        )
        return "/obs/scoped"

    monkeypatch.setattr(analyst_graph, "ensure_run_output_dir", fake_ensure)
    default = "/obs/phytomni/agent_data/test/output"
    host = _host(CREATE_DIR=True, OUTPUT_DIR=default)
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")

    output_dir = await submit_output_dir(
        host,
        {"output_dir": default, "input_fingerprint": ""},
        _identity(),
    )

    assert captured["run_root"] == ""
    assert output_dir == "/obs/scoped/children/part-001"


async def test_standalone_submit_isolates_public_job_under_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public-data jobs keep the fingerprint cache but isolate each EI job."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis_storage"
        ".relay_mode_enabled",
        lambda: True,
    )
    default = "/obs/phytomni/agent_data/test/output"
    host = _host(
        CREATE_DIR=True,
        OUTPUT_DIR=default,
        USER_ID="alice",
        BUCKET_NAME="phytomni",
    )
    identity = RunIdentity.create(
        user_id="alice", scope="analysis_agents_task"
    )
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")
    fingerprint = "a" * 64

    output_dir = await submit_output_dir(
        host,
        {"output_dir": default, "input_fingerprint": fingerprint},
        identity,
    )

    assert f"/agent_data/shared/{fingerprint}/jobs/" in output_dir
    assert identity.run_id in output_dir
    assert f"/agent_data/shared/{fingerprint}/output/children/" not in (
        output_dir
    )
    assert output_dir.endswith("/children/part-001")


async def test_standalone_submit_keeps_user_uploads_off_shared_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User uploads must not land in the cross-tenant fingerprint tree."""
    monkeypatch.setattr(
        "mcp_server_phytomni.agents.shared.analysis_storage"
        ".relay_mode_enabled",
        lambda: True,
    )
    default = "/obs/phytomni/agent_data/test/output"
    host = _host(
        CREATE_DIR=True,
        OUTPUT_DIR=default,
        USER_ID="alice",
        BUCKET_NAME="phytomni",
    )
    identity = RunIdentity.create(
        user_id="alice", scope="analysis_agents_task"
    )
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")

    output_dir = await submit_output_dir(
        host,
        {
            "output_dir": default,
            "input_fingerprint": "a" * 64,
            "obs_file_list": [
                "/obs/phytomni/agent_data/uploads/alice/cell_areas.csv"
            ],
        },
        identity,
    )

    assert "/agent_data/shared/" not in output_dir
    assert "/user_data/alice/" in output_dir
    assert output_dir.endswith("/children/part-001")


async def test_submit_output_dir_uses_child_root_when_path_is_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-default child path is peeled back to its run root."""
    captured: dict[str, Any] = {}

    async def fake_ensure(
        *_args: Any,
        **kwargs: Any,
    ) -> str:
        del kwargs
        captured["run_root"] = _args[3] if len(_args) > 3 else None
        return "/obs/run"

    monkeypatch.setattr(analyst_graph, "ensure_run_output_dir", fake_ensure)
    host = _host(CREATE_DIR=True, OUTPUT_DIR="/obs/default-dump")
    submit_output_dir = getattr(AnalystGraphMixin, "_submit_output_dir")

    output_dir = await submit_output_dir(
        host,
        {
            "output_dir": "/obs/run/children/part-002",
            "input_fingerprint": "fp",
        },
        _identity(),
    )

    assert captured["run_root"] == "/obs/run"
    assert output_dir == "/obs/run/children/part-001"


async def test_upload_submit_meta_returns_both_obs_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both task.yaml and model.yaml uploads are returned as a pair."""
    uploaded: list[str] = []

    async def fake_upload(
        *, content: str, object_name: str, **kwargs: Any
    ) -> str:
        del content, kwargs
        uploaded.append(object_name)
        return f"bucket:/{object_name}"

    monkeypatch.setattr(
        analyst_graph, "upload_analyst_agents_content", fake_upload
    )
    host = SimpleNamespace(
        analyst_config=SimpleNamespace(BUCKET_NAME="bucket"),
        _submit_payload=lambda _state, _out: "task-yaml",
        _submit_coder_payload=lambda: "model-yaml",
    )

    upload_submit_meta = getattr(AnalystGraphMixin, "_upload_submit_meta")
    paths = await upload_submit_meta(host, {}, "/obs/out", _identity())

    assert paths == ("bucket:/task.yaml", "bucket:/model.yaml")
    assert uploaded == ["task.yaml", "model.yaml"]


def test_submit_payload_dumps_literal_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task YAML keeps goal, meta, and format as block scalars."""
    monkeypatch.setattr(
        analyst_graph, "get_prompt", lambda *_args, **_kwargs: "fmt\n"
    )
    host = _host(PROMPT_FILE="prompts.yaml")
    setattr(host, "_submit_meta", getattr(AnalystGraphMixin, "_submit_meta"))
    setattr(
        host,
        "_processed_data_list",
        getattr(AnalystGraphMixin, "_processed_data_list"),
    )
    submit_payload = getattr(AnalystGraphMixin, "_submit_payload")
    payload = submit_payload(
        host,
        {
            "goal_description": "count rows",
            "plan": "step 1",
            "tool_usages": "tool-a",
            "data_list": {
                "obs://bkt/a.tsv": "counts",
                "local.tsv": "plain",
            },
        },
        "/obs/out",
    )

    assert "goal_description: |" in payload
    assert "/obs/bkt/a.tsv: counts" in payload
    assert "local.tsv: plain" in payload
    assert "output_dir: /obs/out" in payload


def test_processed_data_list_rewrites_obs_keys_only() -> None:
    """obs:// keys become /obs/ paths; other keys stay as given."""
    processed = getattr(AnalystGraphMixin, "_processed_data_list")
    items = processed(
        {
            "data_list": {
                "obs://bucket/gene.fa": "fasta",
                "name": "plain",
                7: "numeric-key",
            }
        }
    )

    assert "/obs/bucket/gene.fa: fasta" in items
    assert "name: plain" in items
    assert "7: numeric-key" in items


def test_submit_coder_payload_delegates_to_build_model_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coder YAML is produced by the shared builder, not inlined."""
    monkeypatch.setattr(
        analyst_graph, "build_model_yaml", lambda _sens, _cfg: "model: ok"
    )
    host = SimpleNamespace(sensitive_config=object(), analyst_config=object())

    coder_payload = getattr(AnalystGraphMixin, "_submit_coder_payload")
    assert coder_payload(host) == "model: ok"


async def test_submit_headers_direct_mode_mints_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-relay submit headers include the IAM token."""

    async def fake_token(*, request_timeout: float, region: str) -> str:
        del request_timeout, region
        return "iam-token"

    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(analyst_graph, "get_token", fake_token)
    host = _host(TIMEOUT=5.0, ANALYSIS_REGION="cn-test")

    submit_headers = getattr(AnalystGraphMixin, "_submit_headers")
    headers = await submit_headers(host)

    assert headers == {
        "Content-Type": "application/json",
        "X-Auth-Token": "iam-token",
    }


async def test_submit_headers_relay_mode_omits_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay submit headers never mint an operator IAM token."""
    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: True)

    submit_headers = getattr(AnalystGraphMixin, "_submit_headers")
    headers = await submit_headers(_host())

    assert headers == {"Content-Type": "application/json"}


async def test_post_submit_job_returns_pending_on_201(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 201 create-task response projects the platform id."""

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(status_code=201, json=lambda: {"id": "T-201"})

    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        analyst_graph, "current_outbound_http_client", lambda _pool: object()
    )
    monkeypatch.setattr(
        analyst_graph, "request_response_with_retries", fake_request
    )
    host = _host(
        TIMEOUT=1.0,
        MAX_RETRIES=1,
        ANALYSIS_URL="https://analysis.example/tasks",
        RETRIABLE_CODES=[429],
    )

    post_submit = getattr(AnalystGraphMixin, "_post_submit_job")
    result = await post_submit(
        host, {"Content-Type": "application/json"}, {"name": "j"}, "j", "/out"
    )

    assert result == {
        "task_id": "T-201",
        "task_status": "PENDING",
        "job_name": "j",
        "output_dir": "/out",
    }


async def test_post_submit_job_raises_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None or a non-201 response becomes the submit McpError."""

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(status_code=500, json=lambda: {})

    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        analyst_graph, "current_outbound_http_client", lambda _pool: object()
    )
    monkeypatch.setattr(
        analyst_graph, "request_response_with_retries", fake_request
    )
    host = _host(
        TIMEOUT=1.0,
        MAX_RETRIES=0,
        ANALYSIS_URL="https://analysis.example/tasks",
        RETRIABLE_CODES=[],
    )

    post_submit = getattr(AnalystGraphMixin, "_post_submit_job")
    with pytest.raises(McpError, match="Submission failed after retries"):
        await post_submit(host, {}, {"name": "j"}, "j", "/out")


async def test_post_submit_job_relay_wraps_grant_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay submit forwards a validated grant envelope around the job."""
    captured: dict[str, Any] = {}

    async def post_json(path: str, body: Any, **kwargs: Any) -> dict[str, str]:
        captured.update(path=path, body=body, kwargs=kwargs)
        return {"id": "T-relay"}

    monkeypatch.setattr(analyst_graph, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(
        analyst_graph,
        "current_relay_client",
        lambda: SimpleNamespace(post_json=post_json),
    )

    post_submit = getattr(AnalystGraphMixin, "_post_submit_job")
    result = await post_submit(
        object(),
        {"Content-Type": "application/json"},
        {"name": "job"},
        "job",
        "/obs/out",
        research_grant_sidecar={
            "schema_version": 1,
            "parent_run_id": "run-1",
            "execution_fingerprint": "fp-1",
            "objects": [
                {
                    "dataset_id": "ds",
                    "exact_reference": "obs://b/a.tsv",
                    "grant_id": "g1",
                    "snapshot_digest": "sha",
                }
            ],
        },
    )

    assert result["task_id"] == "T-relay"
    assert captured["path"] == "analysis/tasks"
    assert "analysis_request" in captured["body"]
    assert "research_input_grants" in captured["body"]


def test_relay_analysis_body_rejects_non_mapping_sidecar() -> None:
    """A non-mapping sidecar is an invalid research grant."""
    with pytest.raises(McpError, match="invalid research grant"):
        _relay_analysis_body({"name": "job"}, "not-a-mapping")


def test_relay_analysis_body_rejects_invalid_header() -> None:
    """Wrong schema_version fails before object inspection."""
    with pytest.raises(McpError, match="invalid research grant"):
        _relay_analysis_body(
            {"name": "job"},
            {
                "schema_version": 2,
                "parent_run_id": "run-1",
                "execution_fingerprint": "fp",
                "objects": [{"dataset_id": "ds"}],
            },
        )


def test_relay_analysis_body_rejects_blank_object_fields() -> None:
    """A grant object with a blank required field is invalid."""
    with pytest.raises(McpError, match="invalid research grant"):
        _relay_analysis_body(
            {"name": "job"},
            {
                "schema_version": 1,
                "parent_run_id": "run-1",
                "execution_fingerprint": "fp",
                "objects": [
                    {
                        "dataset_id": "ds",
                        "exact_reference": "obs://b/a.tsv",
                        "grant_id": "",
                        "snapshot_digest": "sha",
                    }
                ],
            },
        )


def test_relay_analysis_body_rejects_non_mapping_object() -> None:
    """Each grant object must itself be a mapping."""
    with pytest.raises(McpError, match="invalid research grant"):
        _relay_analysis_body(
            {"name": "job"},
            {
                "schema_version": 1,
                "parent_run_id": "run-1",
                "execution_fingerprint": "fp",
                "objects": ["not-a-mapping"],
            },
        )


async def test_submit_node_composes_upload_and_post() -> None:
    """submit_node threads output dir, uploads, headers, and the POST."""
    host = SimpleNamespace()

    async def output_dir(_state: Any, _identity: Any) -> str:
        return "/obs/composed"

    async def upload(
        _state: Any, _out: str, _identity: Any
    ) -> tuple[str, str]:
        return ("obs://task.yaml", "obs://model.yaml")

    async def headers() -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def job_data(
        _state: Any, task_path: str, model_path: str
    ) -> tuple[str, dict[str, Any]]:
        return "job-1", {"task": task_path, "model": model_path}

    async def post(
        _headers: dict[str, str],
        job: dict[str, Any],
        job_name: str,
        output_dir: str,
        **options: Any,
    ) -> dict[str, Any]:
        return {
            "task_id": "T-composed",
            "job_name": job_name,
            "output_dir": output_dir,
            "job": job,
            "sidecar": options.get("research_grant_sidecar"),
        }

    setattr(host, "_submit_run_identity", _identity)
    setattr(host, "_submit_output_dir", output_dir)
    setattr(host, "_upload_submit_meta", upload)
    setattr(host, "_submit_headers", headers)
    setattr(host, "_submit_job_data", job_data)
    setattr(host, "_post_submit_job", post)

    result = await AnalystGraphMixin.submit_node(
        host,
        cast(
            Any,
            {"research_grant_sidecar": {"schema_version": 1}},
        ),
    )

    assert result["task_id"] == "T-composed"
    assert result["output_dir"] == "/obs/composed"
    assert result["sidecar"] == {"schema_version": 1}


async def test_tool_retrieve_node_concatenates_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each extracted tool contributes a fenced usage block."""

    async def fake_retrieve(**_kwargs: Any) -> dict[str, Any]:
        return {"doc_list": [{"content": "use --flag"}]}

    monkeypatch.setattr(analyst_graph, "retrieve", fake_retrieve)
    host = _host(
        RETRIEVE_URL="https://retrieve.example",
        TOOL_REPO_ID="repo",
        TOOL_PAGE_NUM=1,
        TOOL_PAGE_SIZE=2,
        FILTER_STRING=None,
        SCOPE="both",
        EXTRA_REPO_IDS=None,
        RERANK_URL="https://rerank.example",
        RERANK_BATCH_SIZE=8,
        SCORE_THRESHOLD=0,
        TIMEOUT=1.0,
        RETRIABLE_CODES=[],
        MAX_RETRIES=0,
    )

    result = await AnalystGraphMixin.tool_retrieve_node(
        host, cast(AnalystState, {"extracted_tools": ["tool-a"]})
    )

    assert "[tool-a Usage START]" in result["tool_usages"]
    assert "use --flag" in result["tool_usages"]
    assert "[tool-a Usage END]" in result["tool_usages"]
