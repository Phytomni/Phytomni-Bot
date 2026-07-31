# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility facades for the context-store public method signatures."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .review_claim import _ReviewClaimRequest
from .review_finalize import _ReviewFinalizeCall
from .review_reservation import _ReviewReservationRequest
from .review_support import _REVIEW_SETTLEMENT_CLAIM_TTL
from .store_core import _RegisterCandidateCall


@dataclass(frozen=True)
class _FacadeSpec:
    """Inputs used to install one public store facade."""

    signature: inspect.Signature
    name: str
    annotations: dict[str, object]
    private_name: str
    request_builder: Any
    doc: str


def _parameter(
    name: str,
    kind: Any,
    annotation: object = inspect.Parameter.empty,
    default: object = inspect.Parameter.empty,
) -> inspect.Parameter:
    """Build one parameter for a compatibility facade signature."""
    return inspect.Parameter(
        name,
        kind=kind,
        annotation=annotation,
        default=default,
    )


def _facade(
    store_type: type[Any],
    spec: _FacadeSpec,
) -> Any:
    """Build a strict facade that retains the historical public signature."""

    def invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Validate the compatibility call before its request seam."""
        bound = spec.signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        request = spec.request_builder(bound.arguments)
        return getattr(self, spec.private_name)(request)

    setattr(invoke, "__signature__", spec.signature)
    setattr(invoke, "__annotations__", spec.annotations)
    setattr(invoke, "__name__", spec.name)
    setattr(invoke, "__qualname__", f"{store_type.__name__}.{spec.name}")
    setattr(invoke, "__module__", store_type.__module__)
    setattr(invoke, "__doc__", spec.doc)
    return invoke


def _register_request(values: Mapping[str, Any]) -> _RegisterCandidateCall:
    """Build the typed request for candidate registration."""
    return _RegisterCandidateCall.from_values(
        (
            values["key"],
            values["turn_id"],
            values["operation"],
            values["stable_thread_id"],
            values["candidate_thread_id"],
            values["mutation_lock_held"],
        )
    )


def _claim_request(values: Mapping[str, Any]) -> _ReviewClaimRequest:
    """Build the typed request for Review settlement claiming."""
    return _ReviewClaimRequest(
        key=values["key"],
        turn_id=values["turn_id"],
        now=values["now"],
        stale_after=values["stale_after"],
        expected_ledger_version=values["expected_ledger_version"],
        expected_base_context_version=values["expected_base_context_version"],
    )


def _reservation_request(
    values: Mapping[str, Any],
) -> _ReviewReservationRequest:
    """Build the typed request for Review settlement reservation."""
    return _ReviewReservationRequest(
        key=values["key"],
        turn_id=values["turn_id"],
        claim_token=values["claim_token"],
        fence_token=values["fence_token"],
        expected_ledger_version=values["expected_ledger_version"],
        expected_base_context_version=values["expected_base_context_version"],
    )


def _finalize_request(values: Mapping[str, Any]) -> _ReviewFinalizeCall:
    """Build the typed request for Review settlement finalization."""
    return _ReviewFinalizeCall(
        key=values["key"],
        turn_id=values["turn_id"],
        claim_token=values["claim_token"],
        state=values["state"],
        report_revision=values["report_revision"],
        fence_token=values["fence_token"],
    )


def _signatures() -> tuple[inspect.Signature, ...]:
    """Return the four stable public signatures in installation order."""
    self_parameter = _parameter(
        "self", inspect.Parameter.POSITIONAL_OR_KEYWORD
    )
    key_parameter = _parameter(
        "key", inspect.Parameter.POSITIONAL_OR_KEYWORD, "str"
    )
    turn_parameter = _parameter(
        "turn_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, "str"
    )
    return (
        inspect.Signature(
            parameters=(
                self_parameter,
                key_parameter,
                turn_parameter,
                _parameter(
                    "operation", inspect.Parameter.POSITIONAL_OR_KEYWORD, "str"
                ),
                _parameter(
                    "stable_thread_id",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    "str",
                ),
                _parameter(
                    "candidate_thread_id",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    "str",
                ),
                _parameter(
                    "mutation_lock_held",
                    inspect.Parameter.KEYWORD_ONLY,
                    "bool",
                    False,
                ),
            ),
            return_annotation="bool",
        ),
        inspect.Signature(
            parameters=(
                self_parameter,
                key_parameter,
                turn_parameter,
                _parameter(
                    "now",
                    inspect.Parameter.KEYWORD_ONLY,
                    "datetime | str | None",
                    None,
                ),
                _parameter(
                    "stale_after",
                    inspect.Parameter.KEYWORD_ONLY,
                    "timedelta",
                    _REVIEW_SETTLEMENT_CLAIM_TTL,
                ),
                _parameter(
                    "expected_ledger_version",
                    inspect.Parameter.KEYWORD_ONLY,
                    "str | None",
                    None,
                ),
                _parameter(
                    "expected_base_context_version",
                    inspect.Parameter.KEYWORD_ONLY,
                    "int | None",
                    None,
                ),
            ),
            return_annotation="ReviewSettlementClaim",
        ),
        inspect.Signature(
            parameters=(
                self_parameter,
                key_parameter,
                turn_parameter,
                _parameter(
                    "claim_token", inspect.Parameter.KEYWORD_ONLY, "str"
                ),
                _parameter(
                    "fence_token", inspect.Parameter.KEYWORD_ONLY, "int"
                ),
                _parameter(
                    "expected_ledger_version",
                    inspect.Parameter.KEYWORD_ONLY,
                    "str | None",
                    None,
                ),
                _parameter(
                    "expected_base_context_version",
                    inspect.Parameter.KEYWORD_ONLY,
                    "int | None",
                    None,
                ),
            ),
            return_annotation="ReviewSettlementClaim",
        ),
        inspect.Signature(
            parameters=(
                self_parameter,
                key_parameter,
                turn_parameter,
                _parameter(
                    "claim_token", inspect.Parameter.KEYWORD_ONLY, "str"
                ),
                _parameter(
                    "state",
                    inspect.Parameter.KEYWORD_ONLY,
                    'Literal["promoted", "rejected", "failed"]',
                ),
                _parameter(
                    "report_revision",
                    inspect.Parameter.KEYWORD_ONLY,
                    "int | None",
                    None,
                ),
                _parameter(
                    "fence_token",
                    inspect.Parameter.KEYWORD_ONLY,
                    "int | None",
                    None,
                ),
            ),
            return_annotation="bool",
        ),
    )


def install_store_facades(store_type: type[Any]) -> None:
    """Install strict public facades after the store class exists."""
    signatures = _signatures()
    specs = (
        _FacadeSpec(
            name="register_review_candidate",
            signature=signatures[0],
            annotations={
                "key": "str",
                "turn_id": "str",
                "operation": "str",
                "stable_thread_id": "str",
                "candidate_thread_id": "str",
                "mutation_lock_held": "bool",
                "return": "bool",
            },
            private_name="_register_review_candidate",
            request_builder=_register_request,
            doc="Register a candidate before Review can write its checkpoint.",
        ),
        _FacadeSpec(
            name="claim_review_settlement",
            signature=signatures[1],
            annotations={
                "key": "str",
                "turn_id": "str",
                "now": "datetime | str | None",
                "stale_after": "timedelta",
                "expected_ledger_version": "str | None",
                "expected_base_context_version": "int | None",
                "return": "ReviewSettlementClaim",
            },
            private_name="_claim_review_settlement",
            request_builder=_claim_request,
            doc="Claim a staged Review marker with a durable compare-and-set.",
        ),
        _FacadeSpec(
            name="reserve_review_settlement",
            signature=signatures[2],
            annotations={
                "key": "str",
                "turn_id": "str",
                "claim_token": "str",
                "fence_token": "int",
                "expected_ledger_version": "str | None",
                "expected_base_context_version": "int | None",
                "return": "ReviewSettlementClaim",
            },
            private_name="_reserve_review_settlement",
            request_builder=_reservation_request,
            doc=(
                "Reserve the staged proposal before a private checkpoint "
                "write."
            ),
        ),
        _FacadeSpec(
            name="finalize_review_settlement",
            signature=signatures[3],
            annotations={
                "key": "str",
                "turn_id": "str",
                "claim_token": "str",
                "state": 'Literal["promoted", "rejected", "failed"]',
                "report_revision": "int | None",
                "fence_token": "int | None",
                "return": "bool",
            },
            private_name="_finalize_review_settlement",
            request_builder=_finalize_request,
            doc=(
                "Finalize only the worker that durably claimed a Review "
                "marker."
            ),
        ),
    )
    for spec in specs:
        setattr(store_type, spec.name, _facade(store_type, spec))


__all__ = ["install_store_facades"]
