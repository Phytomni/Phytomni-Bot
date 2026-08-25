# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Transport entry adapters preserve business values behind one Runtime."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import StreamingResponse


def test_entrypoint_projects_downloadable_artifact_to_opaque_target(
    tmp_path: Path,
) -> None:
    """Existing report envelopes gain a stable private delivery binding."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_target_store_v2 import (
        SQLiteExecutionTargetStore,
    )

    db_path = str(tmp_path / "artifact-entrypoint.db")
    delivery_ref = "obs://phytomni/runs/run-1/report.md"

    async def business_call():
        return {
            "formatted": {"answer": "done"},
            "execution": {
                "artifacts": [
                    {
                        "role": "scientific_report",
                        "name": "report.md",
                        "media_type": "text/markdown",
                        "size_bytes": 12,
                        "downloadable": True,
                        "download_ref": delivery_ref,
                    }
                ]
            },
        }

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-artifact-entrypoint",
            agent_slug="knowledge",
            arguments={"query": "rice"},
            transport="http",
            call=business_call,
        )
    )

    store = SQLiteExecutionTargetStore(db_path)
    with sqlite3.connect(db_path) as connection:
        target_id = connection.execute(
            "SELECT target_id FROM execution_target_bindings_v2 "
            "WHERE owner_ref = ? AND execution_id = ?",
            ("alice", "turn-artifact-entrypoint"),
        ).fetchone()[0]
    assert target_id.startswith("artifact-")
    binding = store.get(
        owner="alice",
        execution_id="turn-artifact-entrypoint",
        kind="artifact",
        target_id=target_id,
    )
    assert binding is not None
    assert binding.delivery_ref == delivery_ref


def test_network_ready_result_projects_report_table_image_and_archive_targets(
    tmp_path: Path,
) -> None:
    """A ready Network result keeps every deliverable behind opaque targets."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    db_path = str(tmp_path / "network-results-entrypoint.db")
    digest = "sha256:" + "3" * 64
    run_root = "obs://phytomni/runs/run-network"
    inventory_ref = (
        f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
        ".phytomni-result-inventory.json"
    )
    artifacts = [
        {
            "role": "scientific_report",
            "name": "network-report.pdf",
            "media_type": "application/pdf",
            "size_bytes": 216_300,
            "download_ref": f"{run_root}/children/part-001/network-report.pdf",
        },
        {
            "role": "scientific_table",
            "name": "top20-genes.csv",
            "media_type": "text/csv",
            "size_bytes": 1_024,
            "download_ref": f"{run_root}/children/part-001/top20-genes.csv",
        },
        {
            "role": "scientific_image",
            "name": "module-network.png",
            "media_type": "image/png",
            "size_bytes": 7_200_000,
            "download_ref": f"{run_root}/children/part-001/module-network.png",
        },
    ]

    async def business_call():
        return {
            "status": "succeeded",
            "result": {
                "formatted": {"answer": "# Network report\n\nCompleted."},
                "execution": {
                    "artifacts": artifacts,
                    "delivery": {
                        "schema_version": 1,
                        "required": True,
                        "status": "ready",
                        "revision": 1,
                        "inventory_digest": digest,
                        "archive": {
                            "role": "result_archive",
                            "name": "network-results.zip",
                            "media_type": "application/zip",
                            "size_bytes": 224_100_000,
                            "downloadable": True,
                            "report_context_eligible": False,
                            "download_ref": f"result-archive:{digest}",
                        },
                        "error_code": None,
                        "retryable": False,
                    },
                },
                "delivery_internal": {
                    "inventory_ref": inventory_ref,
                    "attempts_claimed": 1,
                    "last_error_code": None,
                },
            },
        }

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-network-results",
            agent_slug="network",
            arguments={"to_id": "TO:0000011", "species_code": "osa"},
            transport="supervisor",
            call=business_call,
        )
    )

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT target_kind, target_id, role, name, delivery_ref "
            "FROM execution_target_bindings_v2 "
            "WHERE owner_ref = ? AND execution_id = ? ORDER BY name",
            ("alice", "turn-network-results"),
        ).fetchall()
    assert {row[2] for row in rows} == {
        "scientific_report",
        "scientific_table",
        "scientific_image",
        "result_archive",
    }
    archive = next(row for row in rows if row[2] == "result_archive")
    assert archive[0] == "download"
    assert archive[1].startswith("download-")
    assert archive[3] == "network-results.zip"
    assert archive[4] == (
        f"{run_root}/delivery/{digest.removeprefix('sha256:')}/"
        "network-results.zip"
    )

    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-network-results", owner="alice"
    )
    assert {result.name for result in projection.results} == {
        "network-report.pdf",
        "top20-genes.csv",
        "module-network.png",
        "network-results.zip",
    }
    public_projection = projection.model_dump(mode="json")
    assert run_root not in repr(public_projection)
    assert inventory_ref not in repr(public_projection)


@pytest.mark.parametrize(
    "transport",
    [
        "authenticated_http",
        "expert_router",
        "openai",
        "mcp",
        "background",
        "resume",
        "recovery",
    ],
)
def test_transport_entry_preserves_business_value_and_uses_runtime(
    tmp_path: Path,
    transport: str,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    calls = 0

    async def business_call():
        nonlocal calls
        calls += 1
        return {"unchanged": "business-result"}

    db_path = tmp_path / f"{transport}.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{transport}",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport=transport,
            call=business_call,
        )
    )

    assert value == {"unchanged": "business-result"}
    assert calls == 1
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        f"turn-{transport}", owner="alice", limit=20
    )
    assert page is not None
    assert [event.type.value for event in page.items][-1] == (
        "execution.succeeded"
    )


def test_reported_design_preparation_conflicts_before_runtime_claim(
    tmp_path: Path,
) -> None:
    """Document the admitted/reconcile precursor at the Runtime boundary."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        ExecutionReservationConflictError,
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
    )

    db_path = str(tmp_path / "reported-design-conflict.db")
    execution_id = "turn-fd0d579b-c9f4-4fe7-a22c-a9e9d8f48884"
    query = (
        "Please help me design the protein structure based on evolution "
        "information for gene Os01g0177400."
    )
    prepared_arguments = {
        "user_query": query,
        "obs_file_list": [],
        "resolve_gene_id": True,
        "interop_mode": "off",
        "interop_targets": [],
    }
    durable_arguments = {
        **prepared_arguments,
        "__query": query,
        "__allowed_tools": ["DigitalDesignAgent"],
        "__forced_tool": "DigitalDesignAgent",
        "__agent_slug": "design",
        "__attachment_owner": "alice",
        "__conversation": {
            "schema_version": 1,
            "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
            "turn_id": "60",
            "mode": "expert",
            "requested_agent_id": "DigitalDesignAgent",
            "allowed_agent_ids": ["DigitalDesignAgent"],
        },
    }
    fingerprint = "d" * 64
    repository = SQLiteExecutionReservationRepository(db_path)
    repository.reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=ExecutionCommand(
            agent_slug="design", arguments=durable_arguments
        ),
        durable_command={
            "agent": "design",
            "arguments": durable_arguments,
            "execution_id": execution_id,
            "owner_ref": "alice",
            "fingerprint_version": 2,
            "fingerprint": fingerprint,
        },
    )

    async def business_call() -> dict[str, str]:
        raise AssertionError(
            "business work must not start on identity conflict"
        )

    with pytest.raises(ExecutionReservationConflictError):
        asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=execution_id,
                agent_slug="design",
                arguments=prepared_arguments,
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )
        )

    assert (
        repository.get(owner="alice", execution_id=execution_id).status.value
        == "admitted"
    )
    with sqlite3.connect(db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_events_v2 WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_spans WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM execution_work_units WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()[0]
            == 0
        )


def test_internal_canonical_identity_starts_prepared_design_arguments(
    tmp_path: Path,
) -> None:
    """Prepared business arguments reuse the outer durable reservation."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        CanonicalReservationIdentity,
        bind_canonical_reservation_identity,
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
    )

    db_path = str(tmp_path / "prepared-design-start.db")
    execution_id = "turn-prepared-design"
    durable_arguments = {
        "user_query": "Os01g0177400",
        "obs_file_list": [],
        "__conversation": {"mode": "expert", "turn_id": "60"},
        "__agent_slug": "design",
    }
    prepared_arguments = {
        "user_query": "Os01g0177400",
        "obs_file_list": [],
    }
    command = ExecutionCommand(
        agent_slug="design", arguments=durable_arguments
    )
    fingerprint = "e" * 64
    repository = SQLiteExecutionReservationRepository(db_path)
    original = repository.reserve(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    identity = CanonicalReservationIdentity(
        owner="alice",
        execution_id=execution_id,
        fingerprint_version=2,
        fingerprint=fingerprint,
        command=command,
    )
    with bind_canonical_reservation_identity(identity):
        value = asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id=execution_id,
                agent_slug="design",
                arguments=prepared_arguments,
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint=fingerprint,
            )
        )

    current = repository.get(owner="alice", execution_id=execution_id)
    assert value == {"status": "succeeded"}
    assert calls == 1
    assert current.status.value == "succeeded"
    assert current.command_hash == original.command_hash


@pytest.mark.parametrize(
    "mismatch",
    [
        "owner",
        "execution_id",
        "agent",
        "fingerprint_version",
        "fingerprint",
        "command",
    ],
)
def test_canonical_identity_rejects_true_mismatch_before_business_start(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """The internal carrier cannot weaken any durable replay fence."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        CanonicalReservationIdentity,
        bind_canonical_reservation_identity,
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        ExecutionReservationConflictError,
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.execution_runtime_contracts import (
        ExecutionCommand,
        ExecutionRuntimeError,
    )

    db_path = str(tmp_path / f"identity-mismatch-{mismatch}.db")
    repository = SQLiteExecutionReservationRepository(db_path)
    original_command = ExecutionCommand(
        agent_slug="design", arguments={"user_query": "Os01g0177400"}
    )
    original = repository.reserve(
        owner="alice",
        execution_id="turn-identity-mismatch",
        fingerprint_version=2,
        fingerprint="a" * 64,
        command=original_command,
    )
    values: dict[str, object] = {
        "owner": "alice",
        "execution_id": "turn-identity-mismatch",
        "fingerprint_version": 2,
        "fingerprint": "a" * 64,
        "command": original_command,
    }
    if mismatch == "owner":
        values["owner"] = "mallory"
    elif mismatch == "execution_id":
        values["execution_id"] = "turn-other"
    elif mismatch == "agent":
        values["command"] = ExecutionCommand(
            agent_slug="chat", arguments={"user_query": "Os01g0177400"}
        )
    elif mismatch == "fingerprint_version":
        values["fingerprint_version"] = 3
    elif mismatch == "fingerprint":
        values["fingerprint"] = "b" * 64
    else:
        values["command"] = ExecutionCommand(
            agent_slug="design", arguments={"user_query": "different"}
        )
    identity = CanonicalReservationIdentity(**values)  # type: ignore[arg-type]
    calls = 0

    async def business_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    expected_error = (
        ExecutionReservationConflictError
        if mismatch == "command"
        else ExecutionRuntimeError
    )
    with (
        bind_canonical_reservation_identity(identity),
        pytest.raises(expected_error),
    ):
        asyncio.run(
            invoke_public_agent(
                db_path=db_path,
                owner="alice",
                execution_id="turn-identity-mismatch",
                agent_slug="design",
                arguments={"user_query": "prepared"},
                transport="service_dispatcher",
                call=business_call,
                fingerprint_version=2,
                fingerprint="a" * 64,
            )
        )

    current = repository.get(
        owner="alice", execution_id="turn-identity-mismatch"
    )
    assert calls == 0
    assert current.status.value == "admitted"
    assert current.command_hash == original.command_hash


def test_background_accepted_value_remains_running(tmp_path: Path) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    async def accepted():
        return ({"status": "running", "id": "legacy-run"}, 202)

    db_path = tmp_path / "accepted.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-accepted",
            agent_slug="analyst",
            arguments={"query": "rice"},
            transport="background",
            call=accepted,
        )
    )
    assert value == ({"status": "running", "id": "legacy-run"}, 202)
    assert (
        SQLiteExecutionReservationRepository(str(db_path))
        .get(owner="alice", execution_id="turn-accepted")
        .status.value
        == "running"
    )


def test_runtime_can_adopt_one_preallocated_domain_run_id(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    async def business_call() -> dict[str, str]:
        return {"status": "ok"}

    db_path = tmp_path / "preallocated-run.db"
    result = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-preallocated",
            agent_slug="research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
            run_id="run-research-preallocated",
        )
    )

    assert result == {"status": "ok"}
    record = SQLiteExecutionReservationRepository(str(db_path)).get(
        owner="alice", execution_id="turn-preallocated"
    )
    assert record.run_id == "run-research-preallocated"


def test_equivalent_transports_share_catalog_driver_and_semantic_facts(
    tmp_path: Path,
) -> None:
    """Presentation adapters may differ, but runtime semantics may not."""
    from mcp_server_phytomni.public_agent_catalog import public_agent_spec
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    transports = ("authenticated_http", "expert_router", "mcp")
    semantic_facts: dict[str, list[tuple[Any, ...]]] = {}
    resolved_drivers: dict[str, str] = {}

    async def business_call() -> dict[str, str]:
        return {"answer": "transport-neutral"}

    for transport in transports:
        db_path = tmp_path / f"semantic-{transport}.db"
        result = asyncio.run(
            invoke_public_agent(
                db_path=str(db_path),
                owner="alice",
                execution_id=f"turn-semantic-{transport}",
                agent_slug="chat",
                arguments={
                    "query": "rice",
                    "__assistant_message_id": "msg-semantic",
                },
                transport=transport,
                call=business_call,
            )
        )
        assert result == {"answer": "transport-neutral"}
        spec = public_agent_spec("chat")
        assert spec is not None
        resolved_drivers[transport] = spec.driver
        page = SQLiteExecutionJournal(str(db_path)).list_events(
            f"turn-semantic-{transport}", owner="alice", limit=20
        )
        assert page is not None
        semantic_facts[transport] = [
            (
                event.type.value,
                event.status.value,
                event.source,
                event.summary.key,
                event.public_payload,
            )
            for event in page.items
        ]

    assert set(resolved_drivers.values()) == {"resumable_graph"}
    assert (
        semantic_facts["authenticated_http"] == semantic_facts["expert_router"]
    )
    assert semantic_facts["authenticated_http"] == semantic_facts["mcp"]


def test_native_nested_result_is_published_without_transport_business_copy(
    tmp_path: Path,
) -> None:
    """The adapter projects the existing formatted/execution envelope."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    async def business_call() -> tuple[dict[str, object], int]:
        return (
            {
                "result": {
                    "formatted": {
                        "answer": "durable formatted answer",
                        "follow_up_questions": ["What next?"],
                    },
                    "execution": {
                        "artifacts": [
                            {
                                "id": "artifact-safe-1",
                                "role": "report",
                                "name": "report.md",
                                "media_type": "text/markdown",
                                "size_bytes": 42,
                            },
                            {
                                "id": "C:/private/report.md",
                                "role": "unsafe",
                                "name": "C:/private/report.md",
                            },
                        ]
                    },
                }
            },
            200,
        )

    db_path = tmp_path / "nested-result.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-nested-result",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )
    assert value[1] == 200
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-nested-result", owner="alice", limit=30
    )
    assert page is not None
    message_events = [
        event
        for event in page.items
        if event.type.value == "message.completed"
    ]
    assert len(message_events) == 1
    assert message_events[0].public_payload.model_dump()["text"] == (
        "durable formatted answer"
    )
    artifact_events = [
        event
        for event in page.items
        if event.type.value == "artifact.published"
    ]
    assert len(artifact_events) == 1
    assert artifact_events[0].target is not None
    assert artifact_events[0].target.id == "artifact-safe-1"


def test_cited_result_publishes_safe_references_with_the_completed_message(
    tmp_path: Path,
) -> None:
    """V2 keeps cited answers reconstructible without exposing direct URLs."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    async def business_call() -> dict[str, object]:
        return {
            "result": {
                "formatted": {
                    "answer": "Evidence-backed answer.<sup>1</sup>",
                    "references": [
                        {
                            "file_id": "private-source-identity",
                            "title": "Drought epigenetics",
                            "au": "Smith J",
                            "ti": "Drought epigenetics",
                            "so": "Plant Journal",
                            "py": "2026",
                            "di": "10.1000/safe-doi",
                            "dl": "https://doi.org/10.1000/safe-doi",
                            "formatted_citation": (
                                "Smith J. [https://doi.org/10.1000/safe-doi]"
                            ),
                        }
                    ],
                }
            }
        }

    db_path = tmp_path / "cited-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-cited-result",
            agent_slug="review",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-cited-result", owner="alice", limit=30
    )
    assert page is not None
    completed = next(
        event
        for event in page.items
        if event.type.value == "message.completed"
    )
    assert completed.to_public_dict()["public_payload"]["references"] == [
        {
            "title": "Drought epigenetics",
            "au": "Smith J",
            "ti": "Drought epigenetics",
            "so": "Plant Journal",
            "py": "2026",
            "di": "10.1000/safe-doi",
        }
    ]


def test_data_result_publishes_the_complete_table_as_message_content(
    tmp_path: Path,
) -> None:
    """DataAgent keeps the legacy visible table instead of a count summary."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    async def business_call() -> dict[str, object]:
        return {
            "result": {
                "formatted": {
                    "answer": "1 row x 1 column",
                    "tabular": {
                        "headers": ["transcript_id_1"],
                        "rows": [["Os01t0177400-01"]],
                    },
                }
            }
        }

    db_path = tmp_path / "data-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-data-result",
            agent_slug="data",
            arguments={"query": "list the transcript id"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-data-result", owner="alice", limit=30
    )
    assert page is not None
    completed = next(
        event
        for event in page.items
        if event.type.value == "message.completed"
    )
    assert completed.to_public_dict()["public_payload"]["text"] == (
        '{"headers":["transcript_id_1"],"rows":[["Os01t0177400-01"]]}'
    )

    with sqlite3.connect(db_path) as connection:
        result_json = connection.execute(
            "SELECT result_json FROM runs WHERE execution_id = ?",
            ("turn-data-result",),
        ).fetchone()[0]
    assert '"answer":"1 row x 1 column"' in result_json
    assert '"tabular":{"headers":["transcript_id_1"]' in result_json


def test_data_result_chunks_unicode_table_without_losing_cells(
    tmp_path: Path,
) -> None:
    """A multibyte table remains complete under the 16 KiB event limit."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    cell = "稻" * 7000

    async def business_call() -> dict[str, object]:
        return {
            "formatted": {
                "answer": "1 row x 1 column",
                "tabular": {"headers": ["description"], "rows": [[cell]]},
            }
        }

    db_path = tmp_path / "unicode-data-result.db"
    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-unicode-data-result",
            agent_slug="data",
            arguments={"query": "describe rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-unicode-data-result", owner="alice", limit=30
    )
    assert page is not None
    chunks = [
        event.to_public_dict()["public_payload"]["text"]
        for event in page.items
        if event.type.value in {"message.snapshot", "message.completed"}
    ]
    assert len(chunks) > 1
    assert json.loads("".join(chunks)) == {
        "headers": ["description"],
        "rows": [[cell]],
    }


def test_private_path_answer_is_returned_but_omitted_from_public_journal(
    tmp_path: Path,
) -> None:
    """Runtime projection cannot make transport redaction a write prerequisite."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    private_answer = "loaded /obs/private/chat.pdf"

    async def business_call() -> dict[str, object]:
        return {"formatted": {"answer": private_answer}}

    db_path = tmp_path / "private-answer.db"
    value = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-private-answer",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=business_call,
        )
    )

    assert value == {"formatted": {"answer": private_answer}}
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-private-answer", owner="alice", limit=30
    )
    assert page is not None
    rendered = " ".join(event.model_dump_json() for event in page.items)
    assert private_answer not in rendered
    assert "/obs/private/chat.pdf" not in rendered
    assert [event.type.value for event in page.items][-1] == (
        "execution.succeeded"
    )


def test_business_exception_is_rethrown_but_not_persisted(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )

    async def failing():
        raise ValueError("password=placeholder-business-error")

    db_path = tmp_path / "error.db"
    with pytest.raises(ValueError, match="placeholder-business-error"):
        asyncio.run(
            invoke_public_agent(
                db_path=str(db_path),
                owner="alice",
                execution_id="turn-error",
                agent_slug="chat",
                arguments={"query": "rice"},
                transport="http",
                call=failing,
            )
        )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-error", owner="alice", limit=20
    )
    assert page is not None
    encoded = str([event.to_public_dict() for event in page.items])
    assert "placeholder-business-error" not in encoded
    assert "business_execution_failed" in encoded


def test_nested_public_agent_inherits_execution_unless_new_turn_admitted(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
    )
    from mcp_server_phytomni.runtime.execution_instrumentation_v2 import (
        current_execution_boundary,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        ExecutionReservationNotFoundError,
        SQLiteExecutionReservationRepository,
    )

    db_path = tmp_path / "nested-agent.db"
    observed: list[tuple[str, str, str | None]] = []

    async def outer_business() -> dict[str, object]:
        async def inherited_business() -> str:
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            observed.append(
                (
                    boundary.context.execution_id,
                    boundary.context.agent.slug,
                    boundary.context.parent_span_id,
                )
            )
            return "inherited"

        inherited = await invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-ignored-for-nested-agent",
            agent_slug="knowledge",
            arguments={"query": "nested"},
            transport="nested_agent",
            call=inherited_business,
        )

        async def new_turn_business() -> str:
            boundary = current_execution_boundary(required=True)
            assert boundary is not None
            observed.append(
                (
                    boundary.context.execution_id,
                    boundary.context.agent.slug,
                    boundary.context.parent_span_id,
                )
            )
            return "new-turn"

        new_turn = await invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-explicit-child",
            agent_slug="data",
            arguments={"query": "separate"},
            transport="authenticated_http",
            call=new_turn_business,
            admit_new_user_turn=True,
        )
        return {"inherited": inherited, "new_turn": new_turn}

    result = asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id="turn-parent",
            agent_slug="chat",
            arguments={"query": "parent"},
            transport="authenticated_http",
            call=outer_business,
        )
    )
    assert result == {"inherited": "inherited", "new_turn": "new-turn"}
    assert observed[0][0:2] == ("turn-parent", "knowledge")
    assert observed[0][2] is not None
    assert observed[1][0:2] == ("turn-explicit-child", "data")
    assert observed[1][2] is None

    repository = SQLiteExecutionReservationRepository(str(db_path))
    with pytest.raises(ExecutionReservationNotFoundError):
        repository.get(
            owner="alice", execution_id="turn-ignored-for-nested-agent"
        )
    assert (
        repository.get(
            owner="alice", execution_id="turn-explicit-child"
        ).status.value
        == "succeeded"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        "turn-parent", owner="alice", limit=50
    )
    assert page is not None
    agent_events = [
        event for event in page.items if event.source.value == "agent"
    ]
    assert [event.type.value for event in agent_events] == [
        "span.created",
        "span.started",
        "span.succeeded",
    ]
    assert all(
        event.public_payload.model_dump().get("phase") == "agent.knowledge"
        for event in agent_events
    )


def test_stream_response_settles_runtime_only_after_full_consumption(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.mcp.formatting.agui import (
        custom,
        text_message_content,
    )
    from mcp_server_phytomni.runtime.execution_content_stream_v2 import (
        execution_content_stream_for_db,
    )
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent_stream_response,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )
    from mcp_server_phytomni.runtime.run_registry import RunRegistry

    observed_run_ids: list[str] = []

    async def build_response(run_id: str) -> StreamingResponse:
        observed_run_ids.append(run_id)

        async def body():
            custom(
                "phyto.progress",
                {"phase": "streaming", "current": 1, "total": 2},
            )
            text_message_content("message-1", "first")
            yield b"first"
            text_message_content("message-1", "second")
            yield b"second"

        response = StreamingResponse(body(), media_type="text/event-stream")
        setattr(
            response,
            "runtime_terminal_result",
            lambda: {"formatted": {"answer": "firstsecond"}},
        )
        return response

    async def exercise() -> list[bytes]:
        response = await invoke_public_agent_stream_response(
            db_path=str(tmp_path / "stream-success.db"),
            owner="alice",
            execution_id="turn-stream-success",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="openai_stream",
            call=build_response,
        )
        assert response.media_type == "text/event-stream"
        return [chunk async for chunk in response.body_iterator]

    assert asyncio.run(exercise()) == [b"first", b"second"]
    repository = SQLiteExecutionReservationRepository(
        str(tmp_path / "stream-success.db")
    )
    record = repository.get(owner="alice", execution_id="turn-stream-success")
    assert observed_run_ids == [record.run_id]
    assert record.status.value == "succeeded"
    persisted = RunRegistry(str(tmp_path / "stream-success.db")).get_run(
        record.run_id, owner="alice"
    )
    assert persisted is not None
    assert persisted.result is not None
    assert persisted.result["answer"] == "firstsecond"
    page = SQLiteExecutionJournal(
        str(tmp_path / "stream-success.db")
    ).list_events("turn-stream-success", owner="alice", limit=30)
    assert page is not None
    assert [event.type.value for event in page.items].count(
        "span.progress"
    ) == 1
    content = execution_content_stream_for_db(
        str(tmp_path / "stream-success.db")
    ).list_after(
        owner="alice",
        execution_id="turn-stream-success",
        output_revision=1,
        after_offset=0,
    )
    assert [(frame.offset, frame.delta) for frame in content] == [
        (5, "first"),
        (11, "second"),
    ]


def test_stream_disconnect_does_not_imply_execution_cancellation_or_failure(
    tmp_path: Path,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent_stream_response,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    async def build_response(_run_id: str) -> StreamingResponse:
        async def body():
            yield b"first"
            yield b"unobserved"

        return StreamingResponse(body(), media_type="text/event-stream")

    async def exercise() -> None:
        response = await invoke_public_agent_stream_response(
            db_path=str(tmp_path / "stream-disconnect.db"),
            owner="alice",
            execution_id="turn-stream-disconnect",
            agent_slug="chat",
            arguments={"query": "rice"},
            transport="openai_stream",
            call=build_response,
        )
        iterator = response.body_iterator.__aiter__()
        assert await iterator.__anext__() == b"first"
        closer = getattr(iterator, "aclose")
        await closer()

    asyncio.run(exercise())
    record = SQLiteExecutionReservationRepository(
        str(tmp_path / "stream-disconnect.db")
    ).get(owner="alice", execution_id="turn-stream-disconnect")
    assert record.status.value == "running"


def test_cancelled_stream_response_build_stays_recoverable(
    tmp_path: Path,
) -> None:
    """Cancellation before a response exists must not synthesize FAILED."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent_stream_response,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    db_path = str(tmp_path / "stream-build-cancelled.db")

    async def exercise() -> None:
        started = asyncio.Event()

        async def blocked_build(_run_id: str) -> StreamingResponse:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        task = asyncio.create_task(
            invoke_public_agent_stream_response(
                db_path=db_path,
                owner="alice",
                execution_id="turn-stream-build-cancelled",
                agent_slug="chat",
                arguments={"query": "rice"},
                transport="openai_stream",
                call=blocked_build,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    repository = SQLiteExecutionReservationRepository(db_path)
    current = repository.get(
        owner="alice", execution_id="turn-stream-build-cancelled"
    )
    assert current.status.value == "running"
    assert any(
        record.execution_id == "turn-stream-build-cancelled"
        for record in repository.list_recoverable(limit=10)
    )
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-stream-build-cancelled", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None


def test_cancelled_runtime_operation_stays_recoverable(
    tmp_path: Path,
) -> None:
    """Task cancellation must not be converted into a terminal FAILED fact."""
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
        invoke_public_agent_operation,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    db_path = str(tmp_path / "cancelled-operation.db")

    async def accepted():
        return {"status": "running"}, 202

    asyncio.run(
        invoke_public_agent(
            db_path=db_path,
            owner="alice",
            execution_id="turn-cancelled-operation",
            agent_slug="research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=accepted,
        )
    )
    repository = SQLiteExecutionReservationRepository(db_path)
    baseline = repository.get(
        owner="alice", execution_id="turn-cancelled-operation"
    )

    async def exercise() -> None:
        started = asyncio.Event()

        async def blocked_operation():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            invoke_public_agent_operation(
                db_path=db_path,
                owner="alice",
                execution_id="turn-cancelled-operation",
                agent_slug="research",
                operation="recover",
                action_id="recover-cancelled",
                expected_revision=baseline.supervisor_revision,
                arguments={"source": "supervisor"},
                transport="supervisor",
                call=blocked_operation,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    current = repository.get(
        owner="alice", execution_id="turn-cancelled-operation"
    )
    assert current.status.value == "running"
    assert any(
        record.execution_id == "turn-cancelled-operation"
        for record in repository.list_recoverable(limit=10)
    )
    projection = SQLiteExecutionJournal(db_path).get_projection(
        "turn-cancelled-operation", owner="alice"
    )
    assert projection.status.value == "running"
    assert projection.terminal is None


@pytest.mark.parametrize("operation", ["resume", "recover"])
def test_existing_operation_entry_preserves_business_response(
    tmp_path: Path,
    operation: str,
) -> None:
    from mcp_server_phytomni.runtime.execution_entrypoint_v2 import (
        invoke_public_agent,
        invoke_public_agent_operation,
    )
    from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
        SQLiteExecutionJournal,
    )
    from mcp_server_phytomni.runtime.execution_reservation_v2 import (
        SQLiteExecutionReservationRepository,
    )

    db_path = tmp_path / f"{operation}.db"

    async def admitted():
        return ({"status": "input_required"}, 202)

    asyncio.run(
        invoke_public_agent(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{operation}",
            agent_slug="review" if operation == "resume" else "research",
            arguments={"query": "rice"},
            transport="authenticated_http",
            call=admitted,
        )
    )
    repository = SQLiteExecutionReservationRepository(str(db_path))
    record = repository.get(owner="alice", execution_id=f"turn-{operation}")

    async def business_operation():
        return (
            {
                "status": "succeeded",
                "unchanged": True,
                "result": {
                    "formatted": {
                        "answer": "durable recovery answer",
                    }
                },
            },
            200,
        )

    result = asyncio.run(
        invoke_public_agent_operation(
            db_path=str(db_path),
            owner="alice",
            execution_id=f"turn-{operation}",
            agent_slug="review" if operation == "resume" else "research",
            operation=operation,
            action_id=f"action-{operation}",
            expected_revision=record.supervisor_revision,
            arguments={"approved": True},
            transport=operation,
            call=business_operation,
        )
    )
    assert result == (
        {
            "status": "succeeded",
            "unchanged": True,
            "result": {
                "formatted": {
                    "answer": "durable recovery answer",
                }
            },
        },
        200,
    )
    assert (
        repository.get(
            owner="alice", execution_id=f"turn-{operation}"
        ).status.value
        == "succeeded"
    )
    page = SQLiteExecutionJournal(str(db_path)).list_events(
        f"turn-{operation}", owner="alice", limit=30
    )
    assert page is not None
    completed = [
        event
        for event in page.items
        if event.type.value == "message.completed"
    ]
    assert len(completed) == 1
    assert completed[0].public_payload.model_dump()["text"] == (
        "durable recovery answer"
    )
