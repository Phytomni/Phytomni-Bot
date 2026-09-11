# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Review mixin decode, marker, and claim-precondition edges."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest
from tests.support.sqlite import closed_sqlite_connection

from mcp_server_phytomni.runtime.conversation_context import review_mixin
from mcp_server_phytomni.runtime.conversation_context.review_support import (
    _REVIEW_SETTLEMENT_FENCE_LIMIT,
    _REVIEW_SETTLEMENT_TIMESTAMP_LIMIT,
    _REVIEW_SETTLEMENT_TOKEN_LIMIT,
    _review_candidate_thread_id,
    _ReviewClaimLookupRequest,
    _ReviewMarkerWriteRequest,
)

pytestmark = pytest.mark.unit


class _Fields:
    """Seam used by ``_bounded_marker_fields`` tests."""

    @staticmethod
    def _marker_operation(marker: dict[str, Any]) -> str | None:
        operation = marker.get("operation")
        return operation if isinstance(operation, str) else None

    @staticmethod
    def _marker_stable_thread_id(marker: dict[str, Any]) -> str | None:
        stable = marker.get("stable_thread_id")
        return stable if isinstance(stable, str) else None

    @staticmethod
    def _turn_id_is_bounded(turn_id: str) -> bool:
        return turn_id == "turn-1"

    @staticmethod
    def _report_revision_is_bounded(marker: dict[str, Any]) -> bool:
        return marker.get("report_revision") == 0

    def describe(self) -> str:
        """Return a stable name for the public-method floor."""
        return "review-marker-fields"

    def close(self) -> None:
        """No-op closer so the double meets the public-method floor."""
        return None


def _envelope(stage_metadata: object) -> dict[str, Any]:
    """Build one store envelope with the given stage metadata."""
    return {
        "__conversation_context_store__": {"stage_metadata": stage_metadata}
    }


def test_review_record_rejects_malformed_delta_payloads() -> None:
    """Decode, type, and envelope faults stay fail-closed."""
    assert getattr(review_mixin, "_review_record")("{") is None
    assert getattr(review_mixin, "_review_record")(cast(Any, 1)) is None
    assert getattr(review_mixin, "_review_record")(None) is None
    assert getattr(review_mixin, "_review_record")("[]") is None
    assert getattr(review_mixin, "_review_record")("1") is None
    assert getattr(review_mixin, "_review_record")("{}") is None
    assert (
        getattr(review_mixin, "_review_record")(
            '{"__conversation_context_store__": 1}'
        )
        is None
    )
    assert (
        getattr(review_mixin, "_review_record")(json.dumps(_envelope(1)))
        is None
    )
    assert (
        getattr(review_mixin, "_review_record")(json.dumps(_envelope({})))
        is None
    )
    decoded_marker = getattr(review_mixin, "_review_record")(
        json.dumps(_envelope({"_review_settlement": {"version": 1}}))
    )
    assert decoded_marker is not None
    _decoded, marker = decoded_marker
    assert marker["version"] == 1


def test_with_review_record_and_claim_datetime_edges() -> None:
    """Marker rewrite and clock normalization keep UTC clocks."""
    assert getattr(review_mixin, "_with_review_record")({}, {}) is None
    assert (
        getattr(review_mixin, "_with_review_record")(_envelope({}), {})
        is not None
    )
    assert (
        getattr(review_mixin, "_with_review_record")(
            {"__conversation_context_store__": []},
            {},
        )
        is None
    )
    assert (
        getattr(review_mixin, "_with_review_record")(
            {"__conversation_context_store__": {"stage_metadata": 1}},
            {},
        )
        is None
    )
    rewritten = getattr(review_mixin, "_with_review_record")(
        _envelope({"_review_settlement": {"version": 1}}),
        {"version": 2},
    )
    assert rewritten is not None and '"version":2' in rewritten.replace(
        " ", ""
    )
    naive = getattr(review_mixin, "_claim_datetime")("2026-01-01T00:00:00")
    assert naive.tzinfo is UTC
    offset = timezone(timedelta(hours=8))
    shifted = getattr(review_mixin, "_claim_datetime")(
        datetime(2026, 1, 1, tzinfo=offset)
    )
    assert shifted.tzinfo is UTC
    assert getattr(review_mixin, "_claim_datetime")(None).tzinfo is UTC


def test_write_review_marker_false_and_persists_when_encoded() -> None:
    """A None encoder is rejected; a real rewrite updates the turn row."""

    class _NoneEncoder:
        @staticmethod
        def _with_review_record(_decoded: object, _marker: object) -> None:
            return None

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_NoneEncoder"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    with closed_sqlite_connection(":memory:") as connection:
        connection.execute(
            "CREATE TABLE conversation_turns ("
            "conversation_key TEXT, turn_id TEXT, "
            "delta_json TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO conversation_turns VALUES (?, ?, ?, ?)",
            ("k", "t", "{}", "old"),
        )
        request = _ReviewMarkerWriteRequest(
            connection=connection,
            key="k",
            turn_id="t",
            decoded=_envelope({"_review_settlement": {"version": 1}}),
            marker={"version": 2},
            now="now",
        )
        assert (
            getattr(review_mixin, "_write_review_marker")(
                _NoneEncoder, request
            )
            is False
        )

        class _Encoder:
            _with_review_record = staticmethod(
                getattr(review_mixin, "_with_review_record")
            )

            def describe(self) -> str:
                """Return a stable name for the public-method floor."""
                return "_Encoder"

            def close(self) -> None:
                """No-op closer so the double meets the public-method floor."""
                return None

        assert (
            getattr(review_mixin, "_write_review_marker")(_Encoder, request)
            is True
        )
        stored = connection.execute(
            "SELECT delta_json, updated_at FROM conversation_turns"
        ).fetchone()
        assert stored is not None
        assert stored[1] == "now"
        assert '"version":2' in stored[0].replace(" ", "")


def test_marker_field_helpers_reject_and_accept_bounded_values() -> None:
    """Fence, operation, revision, and field seams stay typed."""
    assert (
        getattr(review_mixin, "_marker_fence")({"settlement_fence": True})
        is None
    )
    assert (
        getattr(review_mixin, "_marker_fence")({"settlement_fence": "2"})
        is None
    )
    assert (
        getattr(review_mixin, "_marker_fence")({"settlement_fence": 0}) is None
    )
    assert (
        getattr(review_mixin, "_marker_fence")(
            {"settlement_fence": _REVIEW_SETTLEMENT_FENCE_LIMIT + 1}
        )
        is None
    )
    assert getattr(review_mixin, "_marker_fence")({"settlement_fence": 2}) == 2
    assert getattr(review_mixin, "_marker_operation")({"version": 2}) is None
    assert (
        getattr(review_mixin, "_marker_operation")(
            {"version": 1, "operation": "x"}
        )
        is None
    )
    assert (
        getattr(review_mixin, "_marker_operation")(
            {"version": 1, "operation": "local_revision"}
        )
        == "local_revision"
    )
    assert getattr(review_mixin, "_report_revision_is_bounded")({}) is False
    assert (
        getattr(review_mixin, "_report_revision_is_bounded")(
            {"report_revision": -1}
        )
        is False
    )
    assert (
        getattr(review_mixin, "_report_revision_is_bounded")(
            {"report_revision": 0}
        )
        is True
    )
    assert (
        getattr(review_mixin, "_bounded_marker_fields")(_Fields, {}, "turn-1")
        is None
    )
    assert (
        getattr(review_mixin, "_bounded_marker_fields")(
            _Fields,
            {
                "operation": "follow_up",
                "stable_thread_id": "ctx-" + "a" * 64,
                "turn_id": "other",
                "report_revision": 0,
            },
            "turn-1",
        )
        is None
    )
    assert (
        getattr(review_mixin, "_bounded_marker_fields")(
            _Fields,
            {
                "operation": "follow_up",
                "stable_thread_id": "ctx-" + "a" * 64,
                "turn_id": "turn-2",
                "report_revision": 0,
            },
            "turn-2",
        )
        is None
    )
    assert (
        getattr(review_mixin, "_bounded_marker_fields")(
            _Fields,
            {
                "operation": "follow_up",
                "stable_thread_id": "ctx-" + "a" * 64,
                "turn_id": "turn-1",
                "report_revision": 1,
            },
            "turn-1",
        )
        is None
    )
    assert getattr(review_mixin, "_bounded_marker_fields")(
        _Fields,
        {
            "operation": "follow_up",
            "stable_thread_id": "ctx-" + "a" * 64,
            "turn_id": "turn-1",
            "report_revision": 0,
        },
        "turn-1",
    ) == ("follow_up", "ctx-" + "a" * 64)


def test_marker_identity_and_expiry_edges() -> None:
    """Stable-key, candidate, expiry, and claim-part bounds stay closed."""
    key = str(UUID("018fdf9e-1f0b-7a63-a5a3-5e4625b43ad7"))
    expected = (
        "ctx-"
        + hashlib.sha256(
            f"conversation-context-v1:{UUID(key)}:ReviewAgent".encode("ascii")
        ).hexdigest()
    )
    assert (
        getattr(review_mixin, "_stable_marker_matches_key")(expected, key)
        is True
    )
    assert (
        getattr(review_mixin, "_stable_marker_matches_key")("x", "not-a-uuid")
        is True
    )
    stable = "ctx-" + "a" * 64
    new_review = {
        "operation": "new_review",
        "stable_thread_id": stable,
        "turn_id": "turn-1",
        "candidate_thread_id": (
            f"{stable}:candidate:"
            + hashlib.sha256(
                f"review-candidate-v1:{stable}:turn-1".encode()
            ).hexdigest()[:32]
        ),
    }
    assert _review_candidate_thread_id(new_review) is not None
    assert (
        getattr(review_mixin, "_marker_candidate_is_bounded")(
            new_review, "new_review"
        )
        is True
    )
    assert (
        getattr(review_mixin, "_marker_candidate_is_bounded")({}, "new_review")
        is False
    )
    assert (
        getattr(review_mixin, "_marker_candidate_is_bounded")(
            {"candidate_thread_id": None}, "follow_up"
        )
        is True
    )

    class _MissingFields:
        @staticmethod
        def _bounded_marker_fields(_marker: object, _turn_id: object) -> None:
            return None

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_MissingFields"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    assert (
        getattr(review_mixin, "_marker_is_bounded")(
            _MissingFields, {}, key="k", turn_id="t"
        )
        is False
    )

    class _Bounded:
        @staticmethod
        def _bounded_marker_fields(_marker: object, _turn_id: object):
            return ("follow_up", "stable")

        @staticmethod
        def _stable_marker_matches_key(_stable: object, _key: object) -> bool:
            return True

        @staticmethod
        def _marker_candidate_is_bounded(
            _marker: object, _operation: object
        ) -> bool:
            return True

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_Bounded"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    assert (
        getattr(review_mixin, "_marker_is_bounded")(
            _Bounded, {}, key="k", turn_id="t"
        )
        is True
    )
    assert getattr(review_mixin, "_claim_is_expired")(
        "not-a-time",
        clock=datetime.now(UTC),
        stale_after=timedelta(seconds=1),
    )

    class _BadClock:
        tzinfo = None

        def replace(self, tzinfo: object) -> None:
            """Reject timezone repair so the claim is treated as expired."""
            del tzinfo
            raise TypeError("bad clock")

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_BadClock"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    assert getattr(review_mixin, "_claim_is_expired")(
        cast(Any, _BadClock()),
        clock=datetime.now(UTC),
        stale_after=timedelta(seconds=1),
    )
    assert getattr(review_mixin, "_bounded_claim_parts")(1, "now", 1) is None
    assert getattr(review_mixin, "_bounded_claim_parts")("", "now", 1) is None
    assert (
        getattr(review_mixin, "_bounded_claim_parts")(
            "t" * (_REVIEW_SETTLEMENT_TOKEN_LIMIT + 1), "now", 1
        )
        is None
    )
    assert getattr(review_mixin, "_bounded_claim_parts")("tok", 1, 1) is None
    assert getattr(review_mixin, "_bounded_claim_parts")("tok", "", 1) is None
    assert (
        getattr(review_mixin, "_bounded_claim_parts")(
            "tok", "c" * (_REVIEW_SETTLEMENT_TIMESTAMP_LIMIT + 1), 1
        )
        is None
    )
    assert (
        getattr(review_mixin, "_bounded_claim_parts")("tok", "now", None)
        is None
    )
    assert getattr(review_mixin, "_bounded_claim_parts")("tok", "now", 3) == (
        "tok",
        "now",
        3,
    )


def test_claim_row_failure_maps_each_precondition() -> None:
    """Tombstone, state, ledger, and version mismatches stay conflicts."""

    class _Tombstoned:
        @staticmethod
        def _review_context_state(_connection: object, _key: object):
            return (1, "tombstoned")

        def describe(self) -> str:
            """Return a stable name for the public-method floor."""
            return "_Tombstoned"

        def close(self) -> None:
            """No-op closer so the double meets the public-method floor."""
            return None

    with closed_sqlite_connection(":memory:") as connection:
        tombstoned = getattr(review_mixin, "_claim_row_failure")(
            _Tombstoned(),
            _ReviewClaimLookupRequest(
                connection=connection,
                key="k",
                row=("staged", "ledger", 1, "d"),
                expected_ledger_version=None,
                expected_base_context_version=None,
            ),
        )
        assert tombstoned is not None and tombstoned.status == "conflict"

        class _Live:
            @staticmethod
            def _review_context_state(_connection: object, _key: object):
                return (2, "active")

            def describe(self) -> str:
                """Return a stable name for the public-method floor."""
                return "_Live"

            def close(self) -> None:
                """No-op closer so the double meets the public-method floor."""
                return None

        failed = getattr(review_mixin, "_claim_row_failure")(
            _Live(),
            _ReviewClaimLookupRequest(
                connection=connection,
                key="k",
                row=("failed", "ledger", 2, "d"),
                expected_ledger_version=None,
                expected_base_context_version=None,
            ),
        )
        assert failed is not None and failed.status == "conflict"
        ledger = getattr(review_mixin, "_claim_row_failure")(
            _Live(),
            _ReviewClaimLookupRequest(
                connection=connection,
                key="k",
                row=("staged", "other", 2, "d"),
                expected_ledger_version="ledger",
                expected_base_context_version=None,
            ),
        )
        assert ledger is not None and ledger.status == "conflict"
        version = getattr(review_mixin, "_claim_row_failure")(
            _Live(),
            _ReviewClaimLookupRequest(
                connection=connection,
                key="k",
                row=("staged", "ledger", 1, "d"),
                expected_ledger_version=None,
                expected_base_context_version=2,
            ),
        )
        assert version is not None and version.status == "conflict"
        current = getattr(review_mixin, "_claim_row_failure")(
            _Live(),
            _ReviewClaimLookupRequest(
                connection=connection,
                key="k",
                row=("staged", "ledger", 0, "d"),
                expected_ledger_version=None,
                expected_base_context_version=None,
            ),
        )
        assert current is not None and current.status == "conflict"
        assert (
            getattr(review_mixin, "_claim_row_failure")(
                _Live(),
                _ReviewClaimLookupRequest(
                    connection=connection,
                    key="k",
                    row=("committed", "ledger", 2, "d"),
                    expected_ledger_version="ledger",
                    expected_base_context_version=2,
                ),
            )
            is None
        )


def test_review_context_state_reads_version_and_state() -> None:
    """The context-state seam returns the stored version pair."""
    with closed_sqlite_connection(":memory:") as connection:
        connection.execute(
            "CREATE TABLE conversation_contexts ("
            "conversation_key TEXT, context_version INTEGER, state TEXT)"
        )
        connection.execute(
            "INSERT INTO conversation_contexts VALUES (?, ?, ?)",
            ("k", 3, "active"),
        )
        row = getattr(review_mixin, "_review_context_state")(connection, "k")
        assert row is not None
        assert tuple(row) == (3, "active")
        assert (
            getattr(review_mixin, "_review_context_state")(
                connection, "missing"
            )
            is None
        )
