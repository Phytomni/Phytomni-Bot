# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""TDD coverage for idempotent Research admission."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import NoReturn, cast

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ParsedResearchInput,
    ResearchInputFailure,
)
from mcp_server_phytomni.api.research_input import (
    ResearchAdmissionOutcome,
    ResearchAdmissionRequest,
    ResearchClientFingerprintInput,
    ResearchInputStore,
    ResearchRequestIdentity,
    admit_research_request,
    compute_research_client_fingerprint,
    lookup_research_admission,
    parse_idempotency_identity,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
)
from mcp_server_phytomni.runtime.research_input_store import (
    ResearchAdmissionReservation,
)
from mcp_server_phytomni.runtime.research_input_store_support import (
    AdmissionLaunchFailure,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry, RunSpec

pytestmark = pytest.mark.unit


def _parsed(query: str = "summarize the inputs") -> ParsedResearchInput:
    """Build a parser result without invoking any I/O seam."""
    return ParsedResearchInput(
        original_query_digest=hashlib.sha256(query.encode()).hexdigest(),
        original_query_length=len(query),
        effective_query=query,
        effective_to_original=tuple(range(len(query))),
        removed_spans=(),
        candidates=(),
    )


def _request(
    key: str = "research-1", query: str = "summarize the inputs"
) -> ResearchAdmissionRequest:
    parsed = _parsed(query)
    identity = parse_idempotency_identity(key, None)
    fingerprint = compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=parsed.original_query_digest,
            original_query_length=parsed.original_query_length,
            managed_asset_ids=("asset-1",),
            locale="en-US",
            interop_mode="off",
            interop_targets=(),
            conversation_identity_digest=None,
        )
    )
    return ResearchAdmissionRequest(
        owner="owner-1",
        identity=identity,
        client_fingerprint=fingerprint,
        original_query=query,
        managed_asset_ids=("asset-1",),
        locale="en-US",
        interop_mode="off",
        interop_targets=(),
        route_source="native",
        parsed_input=parsed,
        managed_snapshot=(),
    )


def _store(tmp_path: Path) -> tuple[ResearchInputStore, str]:
    """Create the shared run database used by admission tests."""
    database = str(tmp_path / "research-admission.db")
    registry = RunRegistry(database)
    registry.create_run(RunSpec("unrelated", "owner-1", "research", "api"))
    return ResearchInputStore(database), database


def _conversation(
    *, turn_id: str = "1", request_id: str = "request-1"
) -> ConversationEnvelopeV1:
    """Build the smallest valid Expert conversation identity."""
    return ConversationEnvelopeV1.model_validate(
        {
            "schema_version": 1,
            "conversation_key": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad6",
            "dialogue_id": "018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7",
            "turn_id": turn_id,
            "request_id": request_id,
            "operation": "append",
            "mode": "expert",
            "current_message": {"content": "Research", "locale": "en-US"},
            "requested_agent_id": "InSilicoResearchAgent",
            "allowed_agent_ids": ["InSilicoResearchAgent", "ChatAgent"],
            "ledger_cursor": 0,
            "ledger_version": "a" * 64,
            "base_business_context_version": 0,
        }
    )


def _conversation_request(
    request: ResearchAdmissionRequest,
    identity: ResearchRequestIdentity,
) -> ResearchAdmissionRequest:
    """Bind the canonical conversation digest into the client fingerprint."""
    return replace(
        request,
        identity=identity,
        client_fingerprint=compute_research_client_fingerprint(
            ResearchClientFingerprintInput(
                original_query_digest=(
                    request.parsed_input.original_query_digest
                ),
                original_query_length=(
                    request.parsed_input.original_query_length
                ),
                managed_asset_ids=request.managed_asset_ids,
                locale=request.locale,
                interop_mode=request.interop_mode,
                interop_targets=request.interop_targets,
                conversation_identity_digest=identity.canonical_digest,
            )
        ),
    )


def test_header_identity_requires_visible_ascii_and_persists_only_digest() -> (
    None
):
    """Header identity is exact, bounded, and never retained as plaintext."""
    short = parse_idempotency_identity("A", None)
    longest = parse_idempotency_identity("A" * 255, None)

    assert short.kind == "header"
    assert len(short.canonical_digest) == 64
    assert longest.kind == "header"
    assert longest.canonical_digest != hashlib.sha256(b"A" * 255).hexdigest()

    with pytest.raises(ValueError):
        parse_idempotency_identity("A" * 256, None)
    with pytest.raises(ValueError):
        parse_idempotency_identity("A B", None)
    with pytest.raises(ValueError):
        parse_idempotency_identity("é", None)


def test_header_identity_is_case_and_byte_exact_and_domain_separated() -> None:
    """Header bytes and conversation tuples use separate hash domains."""
    assert parse_idempotency_identity(
        "Key", None
    ) != parse_idempotency_identity("key", None)
    conversation = parse_idempotency_identity(None, _conversation())
    assert conversation != parse_idempotency_identity("conversation", None)


def test_conversation_identity_uses_turn_and_request() -> None:
    """The authoritative conversation tuple excludes dialogue and operation."""
    first = parse_idempotency_identity(None, _conversation())
    changed_turn = parse_idempotency_identity(None, _conversation(turn_id="2"))
    changed_request = parse_idempotency_identity(
        None, _conversation(request_id="request-2")
    )
    changed_transport = parse_idempotency_identity(
        None,
        _conversation(),
    )
    assert first.canonical_digest != changed_turn.canonical_digest
    assert first.canonical_digest != changed_request.canonical_digest
    assert first.canonical_digest == changed_transport.canonical_digest


def test_missing_non_conversation_identity_is_stable_failure() -> None:
    """Missing non-conversation keys fail before any run can be created."""
    with pytest.raises(ResearchInputFailure) as caught:
        parse_idempotency_identity(None, None)

    assert caught.value.code == "research_idempotency_key_required"
    assert caught.value.http_status_hint == 400


def test_client_fingerprint_excludes_transport_but_binds_semantics() -> None:
    """Only caller-owned semantic fields change the client fingerprint."""
    value = ResearchClientFingerprintInput(
        original_query_digest="q" * 64,
        original_query_length=12,
        managed_asset_ids=("asset-1",),
        locale="en-US",
        interop_mode="auto",
        interop_targets=("mcp-a", "a2a-b"),
        conversation_identity_digest=None,
    )
    same = compute_research_client_fingerprint(value)
    changed = compute_research_client_fingerprint(
        replace(value, interop_targets=("a2a-b", "mcp-a"))
    )

    assert same == compute_research_client_fingerprint(value)
    assert changed != same


def test_admission_replays_without_public_query_or_second_root(
    tmp_path: Path,
) -> None:
    """First admission is durable and exact replay is side-effect free."""
    store, database = _store(tmp_path)
    first = admit_research_request(_request(), store)
    replay = admit_research_request(_request(), store)

    assert first.status_code == 202
    assert first.worker_owner is True
    assert replay.run_id == first.run_id
    assert replay.replay is True
    assert replay.status_code == 202
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (
            2,
        )
        assert connection.execute(
            "SELECT query, request_json FROM runs WHERE run_id = ?",
            (first.run_id,),
        ).fetchone() == (None, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = ?",
            (first.run_id,),
        ).fetchone() == (1,)


def test_same_identity_with_new_fingerprint_conflicts_without_mutation(
    tmp_path: Path,
) -> None:
    """A same-key semantic mismatch returns 409 and preserves the run."""
    store, database = _store(tmp_path)
    first = admit_research_request(_request(), store)

    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(_request(query="a different query"), store)

    assert caught.value.code == "research_idempotency_conflict"
    assert caught.value.http_status_hint == 409
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (
            2,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = ?",
            (first.run_id,),
        ).fetchone() == (1,)


def test_conversation_replay_can_attach_one_alias_but_alias_cannot_cross_turn(
    tmp_path: Path,
) -> None:
    """Conversation identity owns one run while a header alias is one-shot."""
    store, database = _store(tmp_path)
    first_identity = parse_idempotency_identity(None, _conversation())
    first_request = _conversation_request(
        _request("ignored-key"), first_identity
    )
    first = admit_research_request(first_request, store)

    alias_request = replace(
        first_request,
        identity=parse_idempotency_identity("Alias-1", _conversation()),
    )
    replay = admit_research_request(alias_request, store)
    assert replay.run_id == first.run_id
    assert replay.replay is True

    conflicting_alias = replace(
        first_request,
        identity=parse_idempotency_identity("Alias-2", _conversation()),
    )
    with pytest.raises(ResearchInputFailure) as alias_caught:
        admit_research_request(conflicting_alias, store)
    assert alias_caught.value.code == "research_idempotency_conflict"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT alias_digest FROM research_idempotency_bindings "
            "WHERE run_id = ?",
            (first.run_id,),
        ).fetchone() == (alias_request.identity.header_alias_digest,)

    next_identity = parse_idempotency_identity(
        "Alias-1", _conversation(turn_id="2")
    )
    next_turn_request = _conversation_request(first_request, next_identity)
    with pytest.raises(ResearchInputFailure) as lookup_caught:
        lookup_research_admission(
            owner=next_turn_request.owner,
            identity=next_turn_request.identity,
            client_fingerprint=next_turn_request.client_fingerprint,
            store=store,
        )
    assert lookup_caught.value.code == "research_idempotency_conflict"
    assert lookup_caught.value.http_status_hint == 409
    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(next_turn_request, store)
    assert caught.value.code == "research_idempotency_conflict"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runs "
            "WHERE agent = 'research' AND run_id != 'unrelated'"
        ).fetchone() == (1,)


def test_identical_first_admissions_share_one_root_and_run(
    tmp_path: Path,
) -> None:
    """The first-request race has one durable owner and one replay run."""
    store, database = _store(tmp_path)
    request = _request("race-key")
    with ThreadPoolExecutor(max_workers=20) as pool:
        outcomes = list(
            pool.map(
                lambda _index: admit_research_request(request, store),
                range(20),
            )
        )

    assert len({outcome.run_id for outcome in outcomes}) == 1
    assert sum(not outcome.replay for outcome in outcomes) == 1
    with sqlite3.connect(database) as connection:
        run_id = outcomes[0].run_id
        assert connection.execute(
            "SELECT COUNT(*) FROM research_idempotency_bindings"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM research_input_resolutions WHERE run_id = ?",
            (run_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM research_work_units WHERE run_id = ?",
            (run_id,),
        ).fetchone() == (1,)


def test_retry_admission_cas_grants_one_of_eight_workers(
    tmp_path: Path,
) -> None:
    """A retryable root failure can be reclaimed by exactly one owner."""
    store, database = _store(tmp_path)
    request = _request("retry-cas-key")
    admitted = admit_research_request(request, store)
    failure = AdmissionLaunchFailure(
        code="research_input_resolution_unavailable",
        retryable=True,
        status_hint=503,
        stage="input_resolution",
    )
    assert store.mark_admission_launch_failed(admitted.run_id, failure)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda _index: store.retry_admission(
                    admitted.run_id,
                    request.owner,
                    request.identity.canonical_digest,
                    request.client_fingerprint,
                ),
                range(8),
            )
        )

    owners = [outcome for outcome in outcomes if outcome is not None]
    assert owners == [
        ResearchAdmissionReservation(admitted.run_id, False, "running")
    ]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status, error, stage, failure_json, expires_at, revision "
            "FROM runs WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone() == ("running", None, "input_resolution", None, None, 2)


def test_launch_failure_after_root_claim_settles_and_queues_grants(
    tmp_path: Path,
) -> None:
    """A synchronous post-plan failure closes its leased root and grants."""
    store, database = _store(tmp_path)
    admitted = admit_research_request(
        _request("claimed-launch-failure"), store
    )
    fingerprint = "f" * 64
    projection = json.dumps(
        {
            "authority_ids": ["grant-1"],
            "research_grant_binding": {
                "parent_run_id": admitted.run_id,
                "execution_fingerprint": fingerprint,
            },
        },
        sort_keys=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE research_work_units SET state = 'leased', "
            "lease_owner = 'root-owner', attempt = 1, revision = 1 "
            "WHERE run_id = ? AND kind = 'resolve_root'",
            (admitted.run_id,),
        )
        connection.execute(
            "UPDATE research_input_resolutions SET status = 'planning', "
            "last_stage = 'planning', final_projection_json = ?, revision = 1 "
            "WHERE run_id = ?",
            (projection, admitted.run_id),
        )
        connection.execute(
            "UPDATE runs SET stage = 'planning', revision = 1 "
            "WHERE run_id = ?",
            (admitted.run_id,),
        )

    failure = AdmissionLaunchFailure(
        code="research_run_tracking_failed",
        retryable=False,
        status_hint=502,
        stage="planning",
    )

    assert store.mark_admission_launch_failed(admitted.run_id, failure)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status, error, stage FROM runs WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone() == ("failed", "research_run_tracking_failed", None)
        assert connection.execute(
            "SELECT status, failure_code, failure_retryable "
            "FROM research_input_resolutions WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone() == ("failed", "research_run_tracking_failed", 0)
        assert connection.execute(
            "SELECT state, lease_owner, failure_code FROM research_work_units "
            "WHERE run_id = ? AND kind = 'resolve_root'",
            (admitted.run_id,),
        ).fetchone() == (
            "terminal_failed",
            None,
            "research_run_tracking_failed",
        )
        assert connection.execute(
            "SELECT authority_parent_run_id, execution_fingerprint, "
            "grant_ids_json, state FROM research_grant_revocations "
            "WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone() == (
            admitted.run_id,
            fingerprint,
            '["grant-1"]',
            "pending",
        )


def test_admission_validates_query_digest_and_hides_public_query(
    tmp_path: Path,
) -> None:
    """Query integrity is checked and public rows stay query-free."""
    store, database = _store(tmp_path)
    request = _request("integrity-key")
    admitted = admit_research_request(request, store)
    forged = replace(request, original_query="forged public query")
    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(forged, store)
    assert caught.value.code == "research_input_resolution_failed"

    with sqlite3.connect(database) as connection:
        public = connection.execute(
            "SELECT query, request_json FROM runs WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone()
        private = connection.execute(
            "SELECT effective_query, original_query_digest "
            "FROM research_input_resolutions "
            "WHERE run_id = ?",
            (admitted.run_id,),
        ).fetchone()
    assert public == (None, None)
    assert private == (
        request.original_query,
        request.parsed_input.original_query_digest,
    )
    assert "integrity-key" not in str(private)


def test_forged_query_with_old_fingerprint_does_not_replay(
    tmp_path: Path,
) -> None:
    """The raw query is verified before an existing binding is inspected."""
    store, database = _store(tmp_path)
    request = _request("preflight-key")
    first = admit_research_request(request, store)

    forged = replace(request, original_query="different caller query")
    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(forged, store)

    assert caught.value.code == "research_input_resolution_failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (
            2,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM research_idempotency_bindings "
            "WHERE run_id = ?",
            (first.run_id,),
        ).fetchone() == (1,)


def test_legacy_duplicate_blank_owner_digests_migrate_without_rewriting_rows(
    tmp_path: Path,
) -> None:
    """Legacy blank-owner duplicates survive additive migration."""
    database = str(tmp_path / "legacy-research-admission.db")
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                user_id TEXT,
                agent TEXT,
                origin TEXT,
                status TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT,
                updated_at TEXT,
                expires_at TEXT,
                locale TEXT,
                query TEXT,
                request_json TEXT
            );
            CREATE TABLE research_idempotency_bindings (
                run_id TEXT NOT NULL,
                idempotency_digest TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                PRIMARY KEY (run_id, idempotency_digest)
            );
            """)
        connection.executemany(
            "INSERT INTO runs(run_id, status) VALUES (?, 'running')",
            (("legacy-run-1",), ("legacy-run-2",)),
        )
        connection.executemany(
            "INSERT INTO research_idempotency_bindings"
            "(run_id, idempotency_digest, request_digest) VALUES (?, ?, ?)",
            (
                ("legacy-run-1", "same-digest", "request-a"),
                ("legacy-run-2", "same-digest", "request-b"),
            ),
        )
        before = connection.execute(
            "SELECT run_id, idempotency_digest, request_digest "
            "FROM research_idempotency_bindings ORDER BY run_id"
        ).fetchall()

    ResearchInputStore(database)

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT run_id, idempotency_digest, request_digest "
            "FROM research_idempotency_bindings ORDER BY run_id"
        ).fetchall()
    assert after == before


def test_binding_identity_uniqueness_is_scoped_by_operation(
    tmp_path: Path,
) -> None:
    """One owner may reuse a digest in independent operation namespaces."""
    store, database = _store(tmp_path)
    del store
    RunRegistry(database).create_run(
        RunSpec(
            run_id="operation-run-1",
            user_id="owner-1",
            agent="research",
            origin="api",
        )
    )
    RunRegistry(database).create_run(
        RunSpec(
            run_id="operation-run-2",
            user_id="owner-1",
            agent="research",
            origin="api",
        )
    )
    with sqlite3.connect(database) as connection:
        values = (
            ("operation-run-1", "same-digest", "operation-a"),
            ("operation-run-2", "same-digest", "operation-b"),
        )
        for run_id, digest, operation in values:
            connection.execute(
                "INSERT INTO research_idempotency_bindings "
                "(run_id, idempotency_digest, request_digest, owner, "
                "operation, identity_kind, client_fingerprint, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'header', ?, CURRENT_TIMESTAMP)",
                (
                    run_id,
                    digest,
                    "request-digest",
                    "owner-1",
                    operation,
                    "fingerprint-" + operation,
                ),
            )


def test_exact_replay_lookup_precedes_parsed_input_and_asset_resolution() -> (
    None
):
    """Identity-only replay never dereferences parser or asset fields."""
    identity = parse_idempotency_identity("lookup-only", None)
    template = _request("lookup-only")
    request_identity = template.identity
    fingerprint = template.client_fingerprint

    class RecordingStore:
        """Record identity lookup and reject any reserve fallback."""

        def __init__(
            self, result: tuple[bool, ResearchAdmissionReservation | None]
        ) -> None:
            self.result = result

        def lookup_admission(
            self, **request: object
        ) -> tuple[bool, ResearchAdmissionReservation | None]:
            """Return the preconfigured identity lookup result."""
            assert request == {
                "owner": "owner-1",
                "identity_digest": identity.canonical_digest,
                "header_alias_digest": identity.header_alias_digest,
                "client_fingerprint": fingerprint,
            }
            return self.result

        def reserve_admission(self, **request: object) -> NoReturn:
            """Fail if a replay attempts a second durable reservation."""
            raise AssertionError("replay must not reserve or resolve inputs")

    class IdentityOnlyRequest(ResearchAdmissionRequest):
        """Expose caller fields while hiding parser fields on replay."""

        owner = "owner-1"
        identity = request_identity
        client_fingerprint = fingerprint
        original_query = template.original_query
        managed_asset_ids = template.managed_asset_ids
        locale = template.locale
        interop_mode = template.interop_mode
        interop_targets = template.interop_targets
        route_source = template.route_source

        def __getattribute__(self, name: str) -> object:
            """Fail if admission dereferences any post-identity field."""
            if name in {
                "parsed_input",
                "managed_snapshot",
            }:
                raise AssertionError(
                    f"replay touched parser/asset field: {name}"
                )
            return object.__getattribute__(self, name)

        def public_fields(self) -> tuple[object, ...]:
            """Expose the caller fields used by the preflight contract."""
            return (
                self.owner,
                self.identity,
                self.client_fingerprint,
                self.original_query,
            )

    reservation = ResearchAdmissionReservation("existing-run", True, "running")
    store = cast(
        ResearchInputStore,
        RecordingStore((True, reservation)),
    )
    lookup = lookup_research_admission(
        owner="owner-1",
        identity=identity,
        client_fingerprint=fingerprint,
        store=store,
    )
    request = cast(
        ResearchAdmissionRequest, object.__new__(IdentityOnlyRequest)
    )
    outcome = admit_research_request(request, store)

    assert lookup == ResearchAdmissionOutcome(
        run_id="existing-run",
        replay=True,
        worker_owner=False,
        status_code=202,
    )
    assert outcome == ResearchAdmissionOutcome(
        run_id="existing-run",
        replay=True,
        worker_owner=False,
        status_code=202,
    )
    assert cast(IdentityOnlyRequest, request).public_fields()[0] == "owner-1"

    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(
            request,
            cast(
                ResearchInputStore,
                RecordingStore((True, None)),
            ),
        )
    assert caught.value.code == "research_idempotency_conflict"
    assert caught.value.http_status_hint == 409
