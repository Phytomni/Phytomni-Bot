# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Offline safety tests for the fixed DataAgent root-cause probe."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest


def _load_probe_module() -> Any:
    """Load the standalone script without packaging ``scripts/``."""
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/dataagent_root_cause_probe.py"
    )
    spec = importlib.util.spec_from_file_location(
        "dataagent_root_cause_probe", path
    )
    if spec is None or spec.loader is None:
        raise AssertionError("probe module spec is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


class FakeResponse:
    """Small HTTP response fake with real JSON bytes for hashing."""

    def __init__(self, body: dict[str, Any], status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.content = json.dumps(body, sort_keys=True).encode("utf-8")

    def json(self) -> dict[str, Any]:
        """Return the fixture body."""
        return self.body

    def raise_for_status(self) -> None:
        """Mirror the response protocol used by HTTP clients."""


class FakeClient:
    """Capture one request without opening a socket."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.kwargs: dict[str, Any] = {}
        self.request: dict[str, Any] = {}

    async def __aenter__(self) -> FakeClient:
        """Enter the fake async client."""
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: Any,
    ) -> None:
        """Leave the fake async client."""

    async def post(self, path: str, **kwargs: Any) -> FakeResponse:
        """Capture the fixed native request and return its fixture."""
        self.request = {"path": path, **kwargs}
        return self.response


def test_probe_has_no_query_override() -> None:
    """The CLI cannot replace the incident query."""
    parser = probe.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--base-url",
                "https://bot.example",
                "--api-key-env",
                "PHYTOMNI_E2E_API_KEY",
                "--query",
                "replacement",
            ]
        )


def test_probe_uses_exact_incident_identity() -> None:
    """The fixed payload retains both incident identifiers."""
    assert probe.INCIDENT_QUERY == (
        "What is cDNA sequence of Os09t0241100-01 in rice?"
    )
    assert probe.INCIDENT_DIALOGUE_ID == (
        "932a5dc9-d928-481f-83cc-9346dc990dda"
    )
    assert probe.build_incident_payload() == {
        "arguments": {"user_query": probe.INCIDENT_QUERY},
        "dialogue_id": probe.INCIDENT_DIALOGUE_ID,
        "locale": "en-US",
    }


def test_probe_requires_both_live_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing either authorization flag fails before credentials are read."""
    monkeypatch.delenv("PHYTOMNI_RUN_INTEGRATION", raising=False)
    monkeypatch.delenv("PHYTOMNI_ALLOW_NETWORK", raising=False)
    with pytest.raises(probe.ProbeGuardError):
        probe.assert_live_probe_allowed()


@pytest.mark.asyncio
async def test_probe_writes_only_sanitized_metrics() -> None:
    """Headers, SQL, provider details, and sequence never enter evidence."""
    response = FakeResponse(
        {
            "id": "run-data-1",
            "task_ids": ["task-data-1"],
            "result": {
                "formatted": {
                    "tabular": {
                        "headers": ["sequence"],
                        "rows": [["ACGT"]],
                    }
                }
            },
            "stage_summary": [
                {
                    "stage": "database_query",
                    "dependency": "database",
                    "duration_ms": 7,
                    "error_code": None,
                    "error_class": "PrivateProviderError",
                    "final_http_status": 200,
                    "message": "PRIVATE-PROVIDER-BODY",
                }
            ],
            "authorization": "Bearer PRIVATE-KEY",
            "sql": "SELECT PRIVATE_SQL",
            "private_path": "/private/provider/path",
        }
    )
    client = FakeClient(response)

    def factory(**kwargs: Any) -> FakeClient:
        """Return the captured fake and retain client construction options."""
        client.kwargs = kwargs
        return client

    evidence = await probe.run_probe(
        base_url="https://bot.example",
        api_key="PRIVATE-KEY",
        request_id="request-data-1",
        bot_sha="abcdef1",
        client_factory=factory,
    )

    assert client.request["path"] == probe.NATIVE_RUN_PATH
    assert client.request["json"] == probe.build_incident_payload()
    assert client.request["headers"]["Authorization"] == "Bearer PRIVATE-KEY"
    assert client.request["headers"]["X-Request-Id"] == "request-data-1"
    assert client.kwargs["follow_redirects"] is False
    assert evidence.response.run_id == "run-data-1"
    assert evidence.response.task_ids == ("task-data-1",)
    assert evidence.sequence.sequence_length == 4
    assert evidence.sequence.sequence_sha256 == (
        "1dff3e84fe7877e0673b69bbddcf40124e396e3f9943dd890c91b6a09adb9af0"
    )
    assert evidence.sequence.alphabet_valid is True
    assert evidence.stage_summary == (
        {
            "stage": "database_query",
            "dependency": "database",
            "duration_ms": 7,
            "error_class": "PrivateProviderError",
            "final_http_status": 200,
        },
    )
    serialized = json.dumps(asdict(evidence))
    for secret in (
        "PRIVATE-KEY",
        "PRIVATE_SQL",
        "PRIVATE-PROVIDER-BODY",
        "/private/provider/path",
        "ACGT",
    ):
        assert secret not in serialized


def test_probe_projects_safe_error_fields() -> None:
    """Failure evidence keeps only fixed public code and stage fields."""
    body = {
        "error": {
            "code": "upstream_timeout",
            "stage": "database_query",
            "message": "PRIVATE-ERROR-MESSAGE",
        }
    }
    evidence = probe.build_evidence(
        body=body,
        http_status=504,
        request_id="request-data-2",
        bot_sha="abcdef1",
        response_bytes=b"private response",
    )

    assert evidence.response.http_status == 504
    assert evidence.response.error_code == "upstream_timeout"
    assert evidence.response.error_stage == "database_query"
    assert "PRIVATE-ERROR-MESSAGE" not in json.dumps(asdict(evidence))


def test_probe_rejects_malformed_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Malformed URL syntax is treated as an input guard failure."""
    monkeypatch.setenv("PHYTOMNI_RUN_INTEGRATION", "1")
    monkeypatch.setenv("PHYTOMNI_ALLOW_NETWORK", "1")
    monkeypatch.setenv("PHYTOMNI_E2E_API_KEY", "PRIVATE-KEY")
    output = tmp_path / "blocked.json"
    assert (
        probe.main(
            [
                "--base-url",
                "https://[",
                "--api-key-env",
                "PHYTOMNI_E2E_API_KEY",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_probe_writes_sorted_json_with_final_newline(tmp_path: Path) -> None:
    """Evidence serialization is deterministic and canonical."""
    evidence = probe.build_evidence(
        body={},
        http_status=500,
        request_id="request-data-3",
        bot_sha="abcdef1",
        response_bytes=b"{}",
    )
    output = tmp_path / "evidence.json"
    probe.write_evidence(output, evidence)
    raw = output.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["request_id"] == "request-data-3"
    assert raw.index('"alphabet_valid"') < raw.index('"bot_sha"')


def test_main_rejects_missing_guards_before_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Offline invocation cannot read a key or create evidence."""
    monkeypatch.delenv("PHYTOMNI_RUN_INTEGRATION", raising=False)
    monkeypatch.delenv("PHYTOMNI_ALLOW_NETWORK", raising=False)
    output = tmp_path / "blocked.json"
    assert (
        probe.main(
            [
                "--base-url",
                "https://bot.example",
                "--api-key-env",
                "PHYTOMNI_E2E_API_KEY",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
