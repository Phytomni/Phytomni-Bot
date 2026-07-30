# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Durable Review settlement metadata parsing and checkpoint restoration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .conversation_checkpoint import (
    _candidate_thread_id,
    _load_review_checkpoint_state,
    _required_thread_id,
    _required_turn_id,
    _state_values,
)
from .conversation_checkpoint import (
    extract_review_checkpoint as _extract_review_checkpoint,
)
from .conversation_checkpoint import (
    extract_review_report_document as _extract_review_report_document,
)
from .conversation_turn import _usable_report_document
from .conversation_types import review_classes

if TYPE_CHECKING:
    from .conversation import (
        ReviewClarificationError,
        ReviewConversationOperation,
        _RestoredReviewCheckpoint,
        _RestoredReviewSettlement,
    )


def _clarification_error(message: str) -> ReviewClarificationError:
    """Construct the parent module's public clarification exception."""
    return review_classes()[1](message)


def _validate_review_settlement_version(metadata: Mapping[str, Any]) -> None:
    """Require the durable Review marker version supported by this adapter."""
    version = metadata.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != 1
    ):
        raise _clarification_error(
            "Review settlement metadata version is unsupported."
        )


def _review_settlement_operation(
    metadata: Mapping[str, Any],
) -> ReviewConversationOperation:
    """Decode the durable operation while preserving its public error text."""
    operation_value = metadata.get("operation")
    if not isinstance(operation_value, str):
        raise _clarification_error("Review settlement metadata is invalid.")
    operation_type = review_classes()[2]
    try:
        return operation_type(operation_value)
    except ValueError as exc:
        raise _clarification_error(
            "Review settlement metadata is invalid."
        ) from exc


def _review_settlement_state(metadata: Mapping[str, Any]) -> str:
    """Validate the marker state allowed during restart reconstruction."""
    settlement_state = metadata.get("settlement_state")
    if settlement_state not in {"pending", "settling", "promoting"}:
        raise _clarification_error(
            "Review settlement state is invalid for restart."
        )
    return settlement_state


def _review_settlement_identity(
    metadata: Mapping[str, Any],
    *,
    expected_stable_thread_id: str | None,
    expected_turn_id: str | None,
) -> tuple[str, str]:
    """Validate stable and turn identities against the caller's namespace."""
    stable = _required_thread_id(
        metadata.get("stable_thread_id"), "stable"
    )
    turn_id = _required_turn_id(metadata.get("turn_id"))
    if expected_turn_id is not None and turn_id != expected_turn_id:
        raise _clarification_error(
            "Review settlement turn id does not match the staged turn."
        )
    if (
        expected_stable_thread_id is not None
        and stable != expected_stable_thread_id
    ):
        raise _clarification_error(
            "Review settlement stable checkpoint is outside its namespace."
        )
    return stable, turn_id


def _review_settlement_candidate(
    metadata: Mapping[str, Any],
    operation: ReviewConversationOperation,
    stable: str,
    turn_id: str,
) -> str | None:
    """Validate the turn-scoped candidate identity when one is required."""
    candidate_value = metadata.get("candidate_thread_id")
    operation_type = review_classes()[2]
    if operation in {
        operation_type.NEW_REVIEW,
        operation_type.SCOPE_CHANGE,
    }:
        candidate = _required_thread_id(candidate_value, "candidate")
        if candidate != _candidate_thread_id(stable, turn_id):
            raise _clarification_error(
                "Review settlement candidate checkpoint is not turn-scoped."
            )
        return candidate
    if candidate_value is not None:
        raise _clarification_error(
            "Review settlement has an unexpected candidate checkpoint thread."
        )
    return None


def _review_settlement_revision(metadata: Mapping[str, Any]) -> int:
    """Validate the non-negative report revision stored with a marker."""
    report_revision = metadata.get("report_revision")
    if (
        isinstance(report_revision, bool)
        or not isinstance(report_revision, int)
        or report_revision < 0
    ):
        raise _clarification_error(
            "Review settlement revision metadata is invalid."
        )
    return report_revision


def _valid_review_claim_text(value: object) -> bool:
    """Check one bounded textual claim field from durable marker metadata."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 64
    )


def _valid_review_claim_fence(value: object) -> bool:
    """Check the monotonic fence value carried by a durable marker."""
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 1 <= value <= 2**63 - 1
    )


def _valid_review_claim_base_context(value: object) -> bool:
    """Check the base context version carried by a durable marker."""
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= 0
    )


def _validate_review_settlement_claim(
    metadata: Mapping[str, Any], settlement_state: str
) -> None:
    """Validate claim fields only for markers already in-flight."""
    claim_keys = (
        "settlement_claim_token",
        "settlement_claimed_at",
        "settlement_fence",
        "settlement_ledger_version",
        "settlement_base_context_version",
    )
    if settlement_state in {"settling", "promoting"}:
        if not all(
            (
                _valid_review_claim_text(
                    metadata.get("settlement_claim_token")
                ),
                _valid_review_claim_text(
                    metadata.get("settlement_claimed_at")
                ),
                _valid_review_claim_fence(metadata.get("settlement_fence")),
                _valid_review_claim_text(
                    metadata.get("settlement_ledger_version")
                ),
                _valid_review_claim_base_context(
                    metadata.get("settlement_base_context_version")
                ),
            )
        ):
            raise _clarification_error(
                "Review settlement claim metadata is invalid."
            )
        return
    if any(key in metadata for key in claim_keys):
        raise _clarification_error("Review settlement claim metadata is invalid.")


def _parse_review_settlement_metadata(
    metadata: Mapping[str, Any],
    *,
    expected_stable_thread_id: str | None,
    expected_turn_id: str | None,
) -> _RestoredReviewSettlement:
    """Parse and validate durable fields before loading private checkpoints."""
    _validate_review_settlement_version(metadata)
    operation = _review_settlement_operation(metadata)
    settlement_state = _review_settlement_state(metadata)
    stable, turn_id = _review_settlement_identity(
        metadata,
        expected_stable_thread_id=expected_stable_thread_id,
        expected_turn_id=expected_turn_id,
    )
    candidate = _review_settlement_candidate(
        metadata, operation, stable, turn_id
    )
    report_revision = _review_settlement_revision(metadata)
    _validate_review_settlement_claim(metadata, settlement_state)
    settlement_type = review_classes()[6]
    return settlement_type(
        operation=operation,
        stable_thread_id=stable,
        turn_id=turn_id,
        candidate_thread_id=candidate,
        report_revision=report_revision,
    )


async def _load_restored_stable_checkpoint(
    agent: Any,
    operation: ReviewConversationOperation,
    stable_thread_id: str,
) -> _RestoredReviewCheckpoint:
    """Load and validate the active checkpoint before candidate restoration."""
    state = await _load_review_checkpoint_state(agent, stable_thread_id)
    snapshot = _extract_review_checkpoint(state)
    document = _extract_review_report_document(state)
    operation_type = review_classes()[2]
    if (
        operation
        in {
            operation_type.FOLLOW_UP,
            operation_type.LOCAL_REVISION,
            operation_type.SCOPE_CHANGE,
        }
        and snapshot is None
    ):
        raise _clarification_error(
            "The active Review checkpoint is unavailable."
        )
    if operation is not operation_type.NEW_REVIEW and not (
        _usable_report_document(document)
    ):
        raise _clarification_error(
            "The active Review report document is unavailable."
        )
    restored_type = review_classes()[7]
    return restored_type(state, snapshot, document)


async def _load_restored_candidate_checkpoint(
    agent: Any,
    candidate_thread_id: str,
) -> _RestoredReviewCheckpoint:
    """Load and validate the isolated candidate before promotion."""
    state = await _load_review_checkpoint_state(agent, candidate_thread_id)
    values = _state_values(state)
    snapshot = _extract_review_checkpoint(state)
    document = _extract_review_report_document(state)
    if (
        not values
        or snapshot is None
        or document is None
        or not _usable_report_document(document)
    ):
        raise _clarification_error("The Review candidate checkpoint is not ready.")
    restored_type = review_classes()[7]
    return restored_type(state, snapshot, document)
