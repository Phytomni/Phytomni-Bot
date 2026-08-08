# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""TDD coverage for idempotent Research admission."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    ParsedResearchInput,
    ResearchInputFailure,
)
from mcp_server_phytomni.api.research_input import (
    ResearchAdmissionRequest,
    ResearchClientFingerprintInput,
    ResearchInputStore,
    ResearchRequestIdentity,
    admit_research_request,
    compute_research_client_fingerprint,
    parse_idempotency_identity,
)
from mcp_server_phytomni.runtime.conversation_context.models import (
    ConversationEnvelopeV1,
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
    RunRegistry(database).create_run(
        RunSpec(
            run_id="unrelated",
            user_id="owner-1",
            agent="research",
            origin="api",
        )
    )
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

    next_identity = parse_idempotency_identity(
        "Alias-1", _conversation(turn_id="2")
    )
    next_turn_request = _conversation_request(first_request, next_identity)
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


def test_admission_validates_query_digest_and_hides_public_query(
    tmp_path: Path,
) -> None:
    """Query integrity is checked and public rows stay query-free."""
    store, database = _store(tmp_path)
    request = _request("integrity-key")
    forged = replace(request, original_query="forged public query")
    with pytest.raises(ResearchInputFailure) as caught:
        admit_research_request(forged, store)
    assert caught.value.code == "research_input_resolution_failed"

    admitted = admit_research_request(request, store)
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
