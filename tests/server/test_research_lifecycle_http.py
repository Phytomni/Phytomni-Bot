# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP contracts for durable Research lifecycle projection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.api import run_lifecycle
from mcp_server_phytomni.api.lifecycle_contract import (
    ResearchFailureDetail,
    empty_agent_result,
    project_research_lifecycle,
)
from mcp_server_phytomni.api.run_lifecycle import (
    fetch_owner_run,
    project_public_run_record,
)
from mcp_server_phytomni.mcp.formatting.models import ReportExecution
from mcp_server_phytomni.runtime.research_input_store import ResearchInputStore
from mcp_server_phytomni.runtime.run_registry import (
    RunOutcome,
    RunRegistry,
    RunSpec,
)
from mcp_server_phytomni.runtime.run_registry_reports import (
    ReportArtifactSources,
    TerminalReportAssembly,
    _ReportSettlementRequest,
    settle_report_terminal,
)

pytestmark = pytest.mark.server

_STAGES = ("input_resolution", "planning", "execution", "report_assembly")
_STATUSES = ("succeeded", "failed", "cancelled")
_FAILURES = {
    "research_idempotency_key_required": (400, False, "input_resolution"),
    "research_idempotency_conflict": (409, False, "input_resolution"),
    "research_data_block_invalid": (422, False, "input_resolution"),
    "research_dataset_path_invalid": (422, False, "input_resolution"),
    "research_dataset_not_found": (422, False, "input_resolution"),
    "research_dataset_duplicate": (422, False, "input_resolution"),
    "research_dataset_format_unsupported": (422, False, "input_resolution"),
    "research_input_limit_exceeded": (413, False, "input_resolution"),
    "research_document_extraction_failed": (503, True, "input_resolution"),
    "research_input_resolution_failed": (422, False, "input_resolution"),
    "research_input_resolution_unavailable": (503, True, "input_resolution"),
    "research_run_tracking_failed": (502, True, "execution"),
    "research_input_protocol_unavailable": (503, True, "input_resolution"),
    "research_cancel_conflict": (409, False, "execution"),
}


@dataclass(frozen=True, slots=True)
class _FailureCase:
    """Public fixture values for one stable durable failure."""

    code: str
    status_hint: int
    retryable: bool
    stage: str


_FAILURE_CASES = tuple(
    _FailureCase(code, *values) for code, values in _FAILURES.items()
)


def _seed_run(
    db_path: str,
    run_id: str,
    *,
    values: dict[str, Any],
) -> None:
    """Insert one synthetic Research row and its private lifecycle fields."""
    status = values["status"]
    stage = values.get("stage")
    failure = values.get("failure")
    dialogue_id = values.get("dialogue_id")
    RunRegistry(db_path).create_run(
        RunSpec(run_id, "u1", "research", "api"),
        outcome=RunOutcome(status=status, result=empty_agent_result()),
    )
    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET stage = ?, failure_json = ?, dialogue_id = ? "
            "WHERE run_id = ?",
            (
                stage,
                json.dumps(failure) if failure is not None else None,
                dialogue_id,
                run_id,
            ),
        )


@pytest.mark.parametrize("stage", _STAGES)
def test_running_research_projection_exposes_one_stage(
    tmp_path: Any, stage: str
) -> None:
    """A running Research row exposes exactly its bounded current stage."""
    db_path = str(tmp_path / "runs.db")
    _seed_run(
        db_path,
        f"run-{stage}",
        values={"status": "running", "stage": stage},
    )
    record = RunRegistry(db_path).get_run(f"run-{stage}", owner="u1")
    assert record is not None

    projected = project_public_run_record(record, db_path=db_path)
    assert projected["status"] == "running"
    assert projected["stage"] == stage
    assert "failure" not in projected


def test_research_stage_transition_is_strictly_monotonic(
    tmp_path: Any,
) -> None:
    """A fresh stage may advance but never move back to an earlier rank."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-monotonic-stage"
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET stage = 'input_resolution' WHERE run_id = ?",
            (run_id,),
        )

    first = registry.get_run(run_id, owner="u1")
    assert first is not None
    advanced = registry.transition_research_stage(first, "execution")
    assert advanced is not None
    assert advanced.stage == "execution"
    assert advanced.revision == first.revision + 1

    assert (
        registry.transition_research_stage(advanced, "input_resolution")
        is None
    )
    winner = registry.get_run(run_id, owner="u1")
    assert winner is not None
    assert winner.stage == "execution"
    assert winner.revision == advanced.revision


@pytest.mark.parametrize("status", _STATUSES)
def test_terminal_research_projection_hides_current_stage(
    tmp_path: Any, status: str
) -> None:
    """Terminal rows never expose a mutable top-level current stage."""
    db_path = str(tmp_path / "runs.db")
    _seed_run(
        db_path,
        f"run-{status}",
        values={"status": status, "stage": "execution"},
    )
    record = RunRegistry(db_path).get_run(f"run-{status}", owner="u1")
    assert record is not None

    projected = project_public_run_record(record, db_path=db_path)
    assert projected["status"] == status
    assert projected["stage"] is None


@pytest.mark.parametrize(
    "case",
    _FAILURE_CASES,
)
async def test_failed_run_projection_is_identical_and_redacted(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
    case: _FailureCase,
) -> None:
    """GET/list/refresh share one safe failure projection."""
    run_id = f"run-failure-{case.code}"
    dialogue_id = f"dialogue-{case.code}"
    _seed_run(
        tasks_db_path,
        run_id,
        values={
            "status": "failed",
            "stage": "execution",
            "dialogue_id": dialogue_id,
            "failure": {
                "code": case.code,
                "message": "A bounded public failure.",
                "stage": case.stage,
                "retryable": case.retryable,
                "http_status_hint": case.status_hint,
                "last_stage": "provider_private_stage",
                "query": "private-query-sentinel",
                "path": "obs://private-path-sentinel",
                "provider": "provider-private-sentinel",
                "grant": "grant-private-sentinel",
            },
        },
    )
    headers = {"Authorization": f"Bearer {issued_api_key}"}
    detail = await api_client.get(f"/v1/runs/{run_id}", headers=headers)
    listing = await api_client.get(
        f"/v1/runs?dialogue_id={dialogue_id}", headers=headers
    )
    refreshed = await api_client.get(f"/v1/runs/{run_id}", headers=headers)
    debugged = await api_client.get(
        f"/v1/runs/{run_id}?debug=true", headers=headers
    )

    assert (
        detail.status_code
        == listing.status_code
        == refreshed.status_code
        == debugged.status_code
        == 200
    )
    projected = detail.json()
    assert projected == refreshed.json() == listing.json()["data"][0]
    assert projected == debugged.json()
    assert projected["error"] == "run failed"
    assert projected["failure"] == {
        "code": case.code,
        "message": "Research request could not be completed.",
        "stage": case.stage,
        "retryable": case.retryable,
        "http_status_hint": case.status_hint,
    }
    assert projected["stage"] is None
    body = json.dumps(projected)
    for sentinel in (
        "provider_private_stage",
        "private-query-sentinel",
        "obs://private-path-sentinel",
        "provider-private-sentinel",
        "grant-private-sentinel",
    ):
        assert sentinel not in body


def test_research_failure_detail_is_strict_and_bounded() -> None:
    """The public failure DTO rejects private/invalid fields."""
    detail = ResearchFailureDetail(
        code="research_dataset_not_found",
        message="Dataset metadata could not be verified.",
        stage="input_resolution",
        retryable=False,
        http_status_hint=422,
    )
    assert detail.model_dump() == {
        "code": "research_dataset_not_found",
        "message": "Dataset metadata could not be verified.",
        "stage": "input_resolution",
        "retryable": False,
        "http_status_hint": 422,
    }
    with pytest.raises(ValueError):
        ResearchFailureDetail.model_validate(
            {**detail.model_dump(), "path": "private"}
        )
    with pytest.raises(ValueError):
        ResearchFailureDetail.model_validate(
            {**detail.model_dump(), "code": "arbitrary"}
        )


@pytest.mark.parametrize("case", _FAILURE_CASES)
def test_wrong_stable_failure_contract_projects_scalar_only(
    case: _FailureCase,
) -> None:
    """A known code cannot smuggle a mismatched public failure tuple."""
    source = {
        "code": case.code,
        "stage": case.stage,
        "retryable": case.retryable,
        "http_status_hint": case.status_hint,
    }
    wrong_stage = (
        "execution" if case.stage != "execution" else "input_resolution"
    )
    wrong_fields: list[tuple[str, object]] = [("stage", wrong_stage)]
    if case.code != "research_input_resolution_unavailable":
        wrong_fields.append(("retryable", not case.retryable))
    wrong_fields.append(
        ("http_status_hint", 599 if case.status_hint != 599 else 598)
    )
    for field, value in wrong_fields:
        candidate = {**source, field: value}
        _, failure = project_research_lifecycle(
            "failed", "execution", candidate
        )
        assert failure is None


def test_ambiguous_resolution_failure_may_disable_retry() -> None:
    """An ambiguous provider outcome remains safe with retry disabled."""
    _, failure = project_research_lifecycle(
        "failed",
        "execution",
        {
            "code": "research_input_resolution_unavailable",
            "stage": "input_resolution",
            "retryable": False,
            "http_status_hint": 503,
        },
    )
    assert failure == {
        "code": "research_input_resolution_unavailable",
        "message": "Research request could not be completed.",
        "stage": "input_resolution",
        "retryable": False,
        "http_status_hint": 503,
    }


def test_document_extraction_user_failure_contract_projects_full_detail() -> (
    None
):
    """Document input failures may use the documented 422 tuple."""
    _, failure = project_research_lifecycle(
        "failed",
        "execution",
        {
            "code": "research_document_extraction_failed",
            "stage": "input_resolution",
            "retryable": False,
            "http_status_hint": 422,
        },
    )
    assert failure == {
        "code": "research_document_extraction_failed",
        "message": "Research request could not be completed.",
        "stage": "input_resolution",
        "retryable": False,
        "http_status_hint": 422,
    }


async def test_malformed_private_failure_keeps_scalar_only(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """Malformed private JSON never crosses the public projection boundary."""
    run_id = "run-malformed-research-failure"
    _seed_run(
        tasks_db_path,
        run_id,
        values={
            "status": "failed",
            "stage": "execution",
            "failure": {"code": "unknown", "provider": "private-malformed"},
        },
    )
    with closed_sqlite_connection(tasks_db_path) as connection:
        connection.execute(
            "UPDATE runs SET failure_json = ? WHERE run_id = ?",
            ("not-json-private-failure", run_id),
        )
    response = await api_client.get(
        f"/v1/runs/{run_id}",
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 200
    projected = response.json()
    assert projected["error"] == "run failed"
    assert "failure" not in projected
    assert "stage" in projected and projected["stage"] is None
    assert "private-malformed" not in json.dumps(projected)


async def test_cancelled_reconcile_is_terminal_and_does_not_write(
    tmp_path: Any,
) -> None:
    """A cancelled row is not revived or revised by ordinary refresh."""
    db_path = str(tmp_path / "runs.db")
    _seed_run(
        db_path,
        "run-cancelled-no-reconcile",
        values={"status": "cancelled", "stage": "planning"},
    )
    registry = RunRegistry(db_path)
    before = registry.get_run("run-cancelled-no-reconcile", owner="u1")
    assert before is not None
    refreshed = await registry.reconcile(
        "run-cancelled-no-reconcile", owner="u1"
    )
    after = registry.get_run("run-cancelled-no-reconcile", owner="u1")
    assert refreshed == before == after


def test_cancelled_terminal_write_clears_stage_and_gets_purge_ttl(
    tmp_path: Any,
) -> None:
    """Terminal cancellation clears public stage and remains purgeable."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-cancelled-terminal"
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET stage = 'execution' WHERE run_id = ?",
            (run_id,),
        )

    current = registry.get_run(run_id, owner="u1")
    assert current is not None
    assert registry.settle_run(
        run_id,
        owner="u1",
        status="cancelled",
        result=empty_agent_result(),
        expected_revision=current.revision,
    )
    record = registry.get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "cancelled"
    assert record.stage is None
    assert record.timestamps.expires_at is not None

    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )
    assert registry.purge_expired() == 1
    assert registry.get_run(run_id, owner="u1") is None


def test_failed_terminal_write_clears_stage(
    tmp_path: Any,
) -> None:
    """The owner-scoped failure CAS clears stale public stage storage."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-failed-terminal"
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    with closed_sqlite_connection(db_path) as connection:
        connection.execute(
            "UPDATE runs SET stage = 'planning' WHERE run_id = ?",
            (run_id,),
        )

    current = registry.get_run(run_id, owner="u1")
    assert current is not None
    assert registry.fail_running_run(
        run_id,
        owner="u1",
        result=empty_agent_result(),
        error="research_input_resolution_failed",
        expected_revision=current.revision,
    )
    record = registry.get_run(run_id, owner="u1")
    assert record is not None
    assert record.status == "failed"
    assert record.stage is None


def test_late_terminal_callback_cannot_replace_cancelled_run(
    tmp_path: Any,
) -> None:
    """Terminal settlement is a running-row and revision compare-and-set."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-late-terminal-callback"
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    current = registry.get_run(run_id, owner="u1")
    assert current is not None

    assert registry.settle_run(
        run_id,
        owner="u1",
        status="cancelled",
        result=empty_agent_result(),
        expected_revision=current.revision,
    )
    assert not registry.settle_run(
        run_id,
        owner="u1",
        status="failed",
        result=empty_agent_result(),
        expected_revision=current.revision,
    )
    assert not registry.settle_run(
        run_id,
        owner="u1",
        status="failed",
        result=empty_agent_result(),
    )
    assert not registry.fail_running_run(
        run_id,
        owner="u1",
        result=empty_agent_result(),
        error="stale_callback",
    )
    winner = registry.get_run(run_id, owner="u1")
    assert winner is not None
    assert winner.status == "cancelled"


def test_terminal_settlement_without_revision_is_rejected(
    tmp_path: Any,
) -> None:
    """A compatibility settlement cannot win without a revision token."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-settle-without-revision"
    registry = RunRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    current = registry.get_run(run_id, owner="u1")
    assert current is not None
    advanced = registry.transition_research_stage(current, "execution")
    assert advanced is not None

    assert not registry.settle_run(
        run_id,
        owner="u1",
        status="failed",
        result=empty_agent_result(),
    )
    winner = registry.get_run(run_id, owner="u1")
    assert winner == advanced


def test_research_admission_persists_input_resolution_stage(
    tmp_path: Any,
) -> None:
    """A fresh store admission durably starts at input resolution."""
    db_path = str(tmp_path / "runs.db")
    RunRegistry(db_path)
    store = ResearchInputStore(db_path)
    run_id = "run-admission-input-resolution"
    reservation = store.reserve_admission(
        run_id=run_id,
        owner="u1",
        identity_digest="identity-digest",
        identity_kind="header",
        header_alias_digest=None,
        client_fingerprint="client-fingerprint",
        original_query_digest="query-digest",
        original_query_length=7,
        effective_query="query",
        source_map=(),
        candidates=(),
        managed_snapshot=(),
        locale="en-US",
        root_input_digest="query-digest",
    )
    assert reservation is not None
    with closed_sqlite_connection(db_path) as connection:
        row = connection.execute(
            "SELECT stage FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "input_resolution"


async def test_report_settlement_persists_report_assembly_stage(
    tmp_path: Any,
) -> None:
    """Report settlement enters report assembly before terminal CAS."""
    db_path = str(tmp_path / "runs.db")
    run_id = "run-report-assembly-stage"

    class RecordingRegistry(RunRegistry):
        """Capture the durable stage before terminal settlement clears it."""

        def __init__(self, path: str) -> None:
            super().__init__(path)
            self.transitions: list[tuple[str, str]] = []

        def transition_research_stage(self, current: Any, stage: str) -> Any:
            updated = super().transition_research_stage(current, stage)
            if updated is not None:
                self.transitions.append((stage, updated.stage or ""))
            return updated

    registry = RecordingRegistry(db_path)
    registry.create_run(RunSpec(run_id, "u1", "research", "api"))
    current = registry.get_run(run_id, owner="u1")
    assert current is not None

    async def assemble(**_: Any) -> TerminalReportAssembly:
        return TerminalReportAssembly(
            answer="assembled",
            report=ReportExecution(state="final"),
        )

    settled = await settle_report_terminal(
        _ReportSettlementRequest(
            registry=registry,
            current=current,
            status="succeeded",
            live=[],
            sources=ReportArtifactSources(),
            assembler=assemble,
        )
    )
    assert settled is not None
    assert registry.transitions == [("report_assembly", "report_assembly")]
    assert settled.status == "succeeded"
    assert settled.stage is None


async def test_terminal_reconcile_immediately_retries_grant_cleanup(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP read that settles Research also runs post-commit cleanup."""
    db_path = str(tmp_path / "runs.db")
    registry = RunRegistry(db_path)
    registry.create_run(
        RunSpec("run-terminal-cleanup", "u1", "research", "api")
    )
    running = registry.get_run("run-terminal-cleanup", owner="u1")
    assert running is not None
    assert registry.settle_run(
        "run-terminal-cleanup",
        owner="u1",
        status="failed",
        result=empty_agent_result(),
        error="task_failed",
        expected_revision=running.revision,
    )
    terminal = registry.get_run("run-terminal-cleanup", owner="u1")
    assert terminal is not None

    class TransitionRegistry:
        """Expose one running-to-terminal reconcile transition."""

        @staticmethod
        def get_run(_run_id: str, *, owner: str) -> Any:
            """Return the pre-reconcile running record."""
            del owner
            return running

        @staticmethod
        async def reconcile(_run_id: str, *, owner: str) -> Any:
            """Return the post-reconcile terminal record."""
            del owner
            return terminal

    cleaned: list[str] = []

    async def revoke(run_id: str) -> None:
        """Record the post-commit cleanup attempt."""
        cleaned.append(run_id)

    monkeypatch.setattr(
        run_lifecycle, "revoke_registered_research_run", revoke
    )

    payload = await fetch_owner_run(
        "run-terminal-cleanup",
        owner="u1",
        db_path=db_path,
        registry_factory=lambda _path: TransitionRegistry(),
    )

    assert payload["status"] == "failed"
    assert cleaned == ["run-terminal-cleanup"]
