# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Fail-closed public presenter for normalized remote-Agent provider facts."""

from __future__ import annotations

import hashlib

from ..public_agent_catalog import public_agent_spec
from .execution_journal_v2 import (
    ExecutionEventIntentV2,
    ExecutionJournalValidationError,
    parse_execution_event_intent_v2,
)
from .execution_trace_detail import (
    OPERATION_PRESENTER_REGISTRY,
    OperationPresenterCapability,
)
from .execution_work_store_v2 import WorkUnitRecord
from .provider_trace_v2 import ProviderTraceObservation, ProviderTraceRecord

_PUBLIC_SUMMARY_CODES_BY_AGENT = {
    "network": {
        "gene_network.target_validated": "reasoning_summary",
        "gene_network.workflow_selected": "decision",
    },
}


def _presenter_matches_record(
    record: ProviderTraceRecord,
    trace_operations: tuple[str, ...],
    presenter: OperationPresenterCapability,
) -> bool:
    """Check one normalized record against its public presenter contract."""
    if record.semantic_code not in trace_operations:
        return False
    if presenter.operation_key != record.semantic_code:
        return False
    if presenter.semantic_kind not in {"phase", "tool"}:
        return False
    required_kind = {
        "semantic_phase": "phase",
        "semantic_tool": "tool",
    }.get(record.record_class)
    return required_kind is None or presenter.semantic_kind == required_kind


def present_agent_provider_record(
    agent_slug: str,
    unit: WorkUnitRecord,
    observation: ProviderTraceObservation,
    record: ProviderTraceRecord,
    *,
    analysis_span_id: str,
) -> tuple[ExecutionEventIntentV2, ...]:
    """Map one Agent-scoped normalized fact without exposing private IDs."""
    spec = public_agent_spec(agent_slug)
    if spec is None or spec.trace_producer not in {
        "structured_provider",
        "hybrid",
    }:
        return ()
    if record.record_class == "public_summary":
        return _present_explicit_summary(
            agent_slug,
            unit,
            observation,
            record,
            analysis_span_id=analysis_span_id,
        )
    if record.record_class == "artifact_available":
        return ()
    presenter = OPERATION_PRESENTER_REGISTRY.resolve(record.semantic_code)
    if not _presenter_matches_record(
        record,
        spec.trace_operations,
        presenter,
    ):
        return ()

    opaque_key = _opaque_key(unit, observation, record)
    work_unit_id = f"trace-work-{opaque_key[:32]}"
    span_id = f"trace-span-{opaque_key[:32]}"
    common = {
        "source": "provider",
        "span_id": span_id,
        "parent_span_id": analysis_span_id,
        "work_unit_id": work_unit_id,
        "attempt": record.attempt,
    }
    intents = [
        parse_execution_event_intent_v2(
            {
                "type": "work_unit.registered",
                "status": "queued",
                **common,
                "summary": {
                    "key": presenter.label_key,
                    "text": presenter.fallback_label,
                },
                "public_payload": {"operation_key": record.semantic_code},
                "idempotency_key": f"provider-trace:{opaque_key}:registered",
            }
        )
    ]
    status_intent = _operation_status_intent(
        record,
        presenter.label_key,
        presenter.fallback_label,
        opaque_key=opaque_key,
        common=common,
    )
    if status_intent is not None:
        intents.append(status_intent)
    return tuple(intents)


def present_gene_network_provider_record(
    unit: WorkUnitRecord,
    observation: ProviderTraceObservation,
    record: ProviderTraceRecord,
    *,
    analysis_span_id: str,
) -> tuple[ExecutionEventIntentV2, ...]:
    """Keep the stable Gene Network presenter entry point for callers."""
    return present_agent_provider_record(
        "network",
        unit,
        observation,
        record,
        analysis_span_id=analysis_span_id,
    )


def _operation_status_intent(
    record: ProviderTraceRecord,
    label_key: str,
    fallback_label: str,
    *,
    opaque_key: str,
    common: dict[str, object],
) -> ExecutionEventIntentV2 | None:
    if record.status == "pending":
        return None
    event_type: str = {
        "running": "work_unit.acknowledged",
        "succeeded": "work_unit.succeeded",
        "failed": "work_unit.failed",
        "cancelled": "work_unit.cancelled",
        "timed_out": "work_unit.timed_out",
    }[record.status]
    payload: dict[str, object]
    suffix: str = record.status
    if record.status in {"failed", "timed_out"}:
        payload = {
            "code": f"provider_operation_{record.status}",
            "retryable": False,
        }
    else:
        payload = {"operation_key": record.semantic_code}

    if (
        record.status == "running"
        and record.completed is not None
        and record.total is not None
    ):
        capability = OPERATION_PRESENTER_REGISTRY.resolve(record.semantic_code)
        unit = (
            capability.counter_units[0] if capability.counter_units else None
        )
        presented = OPERATION_PRESENTER_REGISTRY.present(
            record.semantic_code,
            progress={
                "completed": record.completed,
                "total": record.total,
                "unit": unit,
            },
        )
        if presented.progress is not None:
            event_type = "work_unit.progress"
            payload = {
                "phase": record.semantic_code,
                **presented.progress,
            }
            suffix = (
                f"running-{record.completed}-{record.total}-"
                f"{presented.progress['unit']}"
            )

    status_key = hashlib.sha256(f"{opaque_key}:{suffix}".encode()).hexdigest()
    terminal_suffix = (
        " completed"
        if record.status == "succeeded"
        else " failed" if record.status in {"failed", "timed_out"} else ""
    )
    return parse_execution_event_intent_v2(
        {
            "type": event_type,
            "status": record.status,
            **common,
            "summary": {
                "key": label_key,
                "text": f"{fallback_label}{terminal_suffix}",
            },
            "public_payload": payload,
            "idempotency_key": f"provider-trace:{status_key}",
        }
    )


def _present_explicit_summary(
    agent_slug: str,
    unit: WorkUnitRecord,
    observation: ProviderTraceObservation,
    record: ProviderTraceRecord,
    *,
    analysis_span_id: str,
) -> tuple[ExecutionEventIntentV2, ...]:
    spec = public_agent_spec(agent_slug)
    if spec is None or spec.public_summary != "explicit":
        return ()
    expected_kind = _PUBLIC_SUMMARY_CODES_BY_AGENT.get(agent_slug, {}).get(
        record.semantic_code
    )
    if expected_kind is None or expected_kind != record.summary_kind:
        return ()
    opaque_key = _opaque_key(unit, observation, record)
    event_type = (
        "reasoning.summary"
        if record.summary_kind == "reasoning_summary"
        else "decision.note"
    )
    summary_text = (
        "Reasoning summary"
        if record.summary_kind == "reasoning_summary"
        else "Decision note"
    )
    try:
        intent = parse_execution_event_intent_v2(
            {
                "type": event_type,
                "status": "running",
                "source": "provider",
                "span_id": analysis_span_id,
                "parent_span_id": unit.parent_span_id,
                "work_unit_id": unit.work_unit_id,
                "attempt": record.attempt,
                "summary": {
                    "key": f"execution.trace.{record.semantic_code}",
                    "text": summary_text,
                },
                "public_payload": {"text": record.public_text},
                "idempotency_key": f"provider-trace:{opaque_key}:summary",
            }
        )
    except ExecutionJournalValidationError:
        return ()
    return (intent,)


def _opaque_key(
    unit: WorkUnitRecord,
    observation: ProviderTraceObservation,
    record: ProviderTraceRecord,
) -> str:
    private_identity = "\x1f".join(
        (
            unit.execution_id,
            unit.work_unit_id,
            observation.adapter_version,
            record.source_identity,
        )
    )
    return hashlib.sha256(private_identity.encode("utf-8")).hexdigest()


__all__ = [
    "present_agent_provider_record",
    "present_gene_network_provider_record",
]
