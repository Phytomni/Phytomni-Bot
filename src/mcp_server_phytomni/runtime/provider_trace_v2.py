# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Finite private contracts for provider trace normalization.

These models are deliberately not public execution DTOs. They keep opaque
provider identities and cursors inside Bot and reject arbitrary provider text
before an Agent-specific presenter can produce public journal facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ProviderTraceRecordClass = Literal[
    "semantic_phase",
    "semantic_tool",
    "bounded_progress",
    "public_summary",
    "artifact_available",
]
ProviderTraceStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
]
ProviderTraceHealth = Literal["healthy", "degraded", "unavailable"]
ProviderTraceSummaryKind = Literal["reasoning_summary", "decision"]
ProviderTraceRejectionCode = Literal[
    "invalid_payload",
    "invalid_record",
    "unknown_record",
    "record_limit",
    "snapshot_limit",
]


class _PrivateFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderTraceRecord(_PrivateFrozenModel):
    """One finite provider-private fact eligible for presenter mapping."""

    source_identity: str = Field(min_length=1, max_length=128)
    record_class: ProviderTraceRecordClass
    semantic_code: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
    )
    status: ProviderTraceStatus
    occurred_at: str | None = Field(default=None, max_length=64)
    attempt: int = Field(default=1, ge=1, le=20)
    completed: int | None = Field(default=None, ge=0, le=1_000_000)
    total: int | None = Field(default=None, ge=0, le=1_000_000)
    summary_kind: ProviderTraceSummaryKind | None = None
    public_text: str | None = Field(default=None, min_length=1, max_length=512)
    explicit_public: bool = False
    artifact_role: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be ISO-8601") from exc
        if parsed.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_finite_shape(self) -> ProviderTraceRecord:
        if (
            self.completed is not None
            and self.total is not None
            and self.completed > self.total
        ):
            raise ValueError("completed cannot exceed total")
        is_summary = self.record_class == "public_summary"
        if is_summary and (
            not self.explicit_public
            or self.summary_kind is None
            or self.public_text is None
        ):
            raise ValueError("public summary requires explicit public content")
        if not is_summary and (
            self.explicit_public
            or self.summary_kind is not None
            or self.public_text is not None
        ):
            raise ValueError("public summary fields require public_summary")
        if (self.record_class == "artifact_available") != (
            self.artifact_role is not None
        ):
            raise ValueError("artifact role requires artifact_available")
        return self


class ProviderTraceObservation(_PrivateFrozenModel):
    """One bounded status-independent trace observation."""

    schema_version: Literal[1]
    adapter_version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9-]*-v[1-9][0-9]*$",
    )
    source_revision: int | None = Field(default=None, ge=0)
    next_cursor: str | None = Field(default=None, max_length=128)
    snapshot_complete: bool
    health: ProviderTraceHealth
    records: tuple[ProviderTraceRecord, ...] = Field(
        default_factory=tuple, max_length=256
    )

    @model_validator(mode="after")
    def validate_record_identities(self) -> ProviderTraceObservation:
        identities = tuple(record.source_identity for record in self.records)
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate source identity in observation")
        return self


class ProviderTraceRejection(_PrivateFrozenModel):
    """Bounded private diagnostic without provider-controlled content."""

    index: int = Field(ge=0, le=1_000_000)
    code: ProviderTraceRejectionCode


class ProviderTraceDiagnostics(_PrivateFrozenModel):
    """Aggregate adapter rejection telemetry safe for private persistence."""

    rejected_records: int = Field(default=0, ge=0, le=1_000_000)
    rejections: tuple[ProviderTraceRejection, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @model_validator(mode="after")
    def validate_rejection_count(self) -> ProviderTraceDiagnostics:
        if self.rejected_records < len(self.rejections):
            raise ValueError("rejected record count is inconsistent")
        return self


class ProviderTraceAdapterResult(_PrivateFrozenModel):
    """Normalized observation plus content-free private diagnostics."""

    observation: ProviderTraceObservation
    diagnostics: ProviderTraceDiagnostics = Field(
        default_factory=ProviderTraceDiagnostics
    )
    overlap_identities: tuple[str, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @field_validator("overlap_identities")
    @classmethod
    def validate_overlap(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not identity or len(identity) > 128 for identity in value):
            raise ValueError("invalid overlap identity")
        return value


class ProviderTraceCheckpoint(_PrivateFrozenModel):
    """Private persisted input shared by full and delta adapters."""

    adapter_version: str | None = Field(default=None, max_length=32)
    cursor: str | None = Field(default=None, max_length=128)
    source_revision: int = Field(default=0, ge=0)
    overlap_identities: tuple[str, ...] = Field(
        default_factory=tuple, max_length=64
    )

    @classmethod
    def from_result(
        cls, result: ProviderTraceAdapterResult
    ) -> ProviderTraceCheckpoint:
        return cls(
            adapter_version=result.observation.adapter_version,
            cursor=result.observation.next_cursor,
            source_revision=result.observation.source_revision or 0,
            overlap_identities=result.overlap_identities,
        )


ProviderTraceNormalizer = Callable[
    [object, str, int], ProviderTraceRecord | None
]


class FullSnapshotProviderTraceAdapter:
    """Convert a bounded full snapshot into only newly observed records."""

    def __init__(
        self,
        *,
        adapter_version: str,
        normalize_record: ProviderTraceNormalizer,
        max_snapshot_records: int = 8192,
        max_record_bytes: int = 16384,
        overlap_size: int = 64,
    ) -> None:
        _validate_adapter_version(adapter_version)
        if not 1 <= max_snapshot_records <= 10000:
            raise ValueError("invalid maximum snapshot records")
        if not 256 <= max_record_bytes <= 65536:
            raise ValueError("invalid maximum record bytes")
        if not 1 <= overlap_size <= 64:
            raise ValueError("invalid overlap size")
        self.adapter_version = adapter_version
        self.normalize_record = normalize_record
        self.max_snapshot_records = max_snapshot_records
        self.max_record_bytes = max_record_bytes
        self.overlap_size = overlap_size

    def adapt(
        self,
        snapshot: Sequence[object],
        *,
        checkpoint: ProviderTraceCheckpoint | None = None,
    ) -> ProviderTraceAdapterResult:
        if isinstance(snapshot, (str, bytes, bytearray)):
            raise ValueError("snapshot must be a record sequence")
        prior = checkpoint or ProviderTraceCheckpoint()
        if (
            prior.adapter_version is not None
            and prior.adapter_version != self.adapter_version
        ):
            prior = ProviderTraceCheckpoint()
        raw_records = tuple(snapshot)
        rejections: list[ProviderTraceRejection] = []
        rejected_count = 0
        if len(raw_records) > self.max_snapshot_records:
            rejected_count += len(raw_records) - self.max_snapshot_records
            rejections.append(
                ProviderTraceRejection(index=0, code="snapshot_limit")
            )
            raw_records = raw_records[-self.max_snapshot_records :]

        identities: list[str] = []
        valid_raw: list[tuple[int, object, str]] = []
        for index, raw in enumerate(raw_records):
            identity = self._record_identity(raw, index)
            if identity is None:
                rejected_count += 1
                if len(rejections) < 64:
                    rejections.append(
                        ProviderTraceRejection(
                            index=index, code="invalid_record"
                        )
                    )
                identity = f"invalid:{index}"
            else:
                valid_raw.append((index, raw, identity))
            identities.append(identity)

        start = _snapshot_new_start(tuple(identities), prior)
        normalized: list[ProviderTraceRecord] = []
        for index, raw, identity in valid_raw:
            if index < start:
                continue
            try:
                record = self.normalize_record(raw, identity, index)
            except (TypeError, ValueError, ValidationError):
                record = None
            if record is None:
                rejected_count += 1
                if len(rejections) < 64:
                    rejections.append(
                        ProviderTraceRejection(
                            index=index, code="unknown_record"
                        )
                    )
                continue
            normalized.append(record)
            if len(normalized) >= 256:
                remaining = sum(
                    1 for valid_index, _, _ in valid_raw if valid_index > index
                )
                rejected_count += remaining
                if len(rejections) < 64 and remaining:
                    rejections.append(
                        ProviderTraceRejection(
                            index=index + 1, code="record_limit"
                        )
                    )
                break

        revision = prior.source_revision + 1
        health: ProviderTraceHealth = (
            "degraded"
            if any(
                rejection.code
                in {"invalid_record", "record_limit", "snapshot_limit"}
                for rejection in rejections
            )
            else "healthy"
        )
        overlap = tuple(identities[-self.overlap_size :])
        return ProviderTraceAdapterResult(
            observation=ProviderTraceObservation(
                schema_version=1,
                adapter_version=self.adapter_version,
                source_revision=revision,
                next_cursor=f"full:{len(identities)}",
                snapshot_complete=True,
                health=health,
                records=tuple(normalized),
            ),
            diagnostics=ProviderTraceDiagnostics(
                rejected_records=rejected_count,
                rejections=tuple(rejections),
            ),
            overlap_identities=overlap,
        )

    def _record_identity(self, raw: object, index: int) -> str | None:
        if isinstance(raw, Mapping):
            stable = raw.get("id")
            if (
                isinstance(stable, str)
                and 1 <= len(stable) <= 119
                and re.fullmatch(r"[A-Za-z0-9_.-]+", stable)
            ):
                return f"provider:{stable}"
        try:
            encoded = json.dumps(
                raw,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        if len(encoded) > self.max_record_bytes:
            return None
        digest = hashlib.sha256(encoded).hexdigest()
        return f"snapshot:{digest}"


class StructuredDeltaProviderTraceAdapter:
    """Validate an already-structured provider delta without raw fallback."""

    def __init__(self, *, adapter_version: str) -> None:
        _validate_adapter_version(adapter_version)
        self.adapter_version = adapter_version

    def adapt(
        self,
        payload: Mapping[str, Any],
        *,
        checkpoint: ProviderTraceCheckpoint | None = None,
    ) -> ProviderTraceAdapterResult:
        observation = ProviderTraceObservation.model_validate(payload)
        if observation.adapter_version != self.adapter_version:
            raise ValueError("provider trace adapter version mismatch")
        prior = checkpoint or ProviderTraceCheckpoint()
        if (
            prior.adapter_version is not None
            and prior.adapter_version != self.adapter_version
        ):
            prior = ProviderTraceCheckpoint()
        if (
            prior.adapter_version == self.adapter_version
            and observation.source_revision is not None
            and observation.source_revision < prior.source_revision
        ):
            raise ValueError("stale provider trace revision")
        seen = set(prior.overlap_identities)
        records = tuple(
            record
            for record in observation.records
            if record.source_identity not in seen
        )
        combined = [
            *prior.overlap_identities,
            *(record.source_identity for record in records),
        ]
        overlap = tuple(dict.fromkeys(combined))[-64:]
        return ProviderTraceAdapterResult(
            observation=observation.model_copy(update={"records": records}),
            overlap_identities=overlap,
        )


def _snapshot_new_start(
    identities: tuple[str, ...], checkpoint: ProviderTraceCheckpoint
) -> int:
    if checkpoint.adapter_version is None:
        return 0
    prior_count = _full_cursor_count(checkpoint.cursor)
    overlap = checkpoint.overlap_identities
    if prior_count is not None and prior_count <= len(identities):
        overlap_start = max(0, prior_count - len(overlap))
        if identities[overlap_start:prior_count] == overlap:
            return prior_count
    for size in range(min(len(overlap), len(identities)), 0, -1):
        needle = overlap[-size:]
        for start in range(len(identities) - size, -1, -1):
            if identities[start : start + size] == needle:
                return start + size
    return 0


def _full_cursor_count(cursor: str | None) -> int | None:
    if cursor is None or not cursor.startswith("full:"):
        return None
    try:
        count = int(cursor.removeprefix("full:"))
    except ValueError:
        return None
    return count if count >= 0 else None


def _validate_adapter_version(value: str) -> None:
    ProviderTraceObservation(
        schema_version=1,
        adapter_version=value,
        snapshot_complete=True,
        health="healthy",
    )


__all__ = [
    "ProviderTraceAdapterResult",
    "ProviderTraceCheckpoint",
    "ProviderTraceDiagnostics",
    "FullSnapshotProviderTraceAdapter",
    "ProviderTraceHealth",
    "ProviderTraceObservation",
    "ProviderTraceRecord",
    "ProviderTraceRecordClass",
    "ProviderTraceRejection",
    "ProviderTraceRejectionCode",
    "ProviderTraceStatus",
    "ProviderTraceSummaryKind",
    "StructuredDeltaProviderTraceAdapter",
]
