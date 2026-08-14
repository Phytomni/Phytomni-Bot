# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Universal failure-channel projection for parallel-dispatch agents.

Exposes ``project_universal_failure_metadata`` so per-agent formatters
in :mod:`result_formatting` can emit one shared ``status`` /
``succeeded_count`` / ``failed_count`` / ``failures`` shape across the
four parallel-dispatch agents (design / network / research / review).
Lives in its own module so :mod:`result_formatting` stays under the
1000-line module size budget enforced by pylint C0302.
"""

import math
from collections.abc import Mapping
from typing import Any, Literal

from ..agents.shared.parallel_dispatch import (
    redact_failure_message,
)

__all__ = [
    "project_interop_metadata",
    "project_universal_failure_metadata",
    "redact_failure_message",
]

_UniversalStatus = Literal["SUCCESS", "PARTIAL", "FAILED", "PENDING"]


def project_universal_failure_metadata(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Project failures + task_ids into client-facing metadata keys.

    Used by per-agent formatters (design / network / research / review)
    to expose the same shape across the four parallel-dispatch agents.
    The returned dict is intended to be merged into ``metadata`` by the
    caller. ``traceback_digest`` is stripped here — it lives only in
    ``raw.phytomni_state``, never in ``formatted.metadata`` — and each
    ``message`` is passed through ``redact_failure_message`` so backend
    URLs and secret-like fragments never reach client metadata.

    Status derivation:
        SUCCESS: failures empty AND task_ids non-empty
        PARTIAL: failures non-empty AND task_ids non-empty
        FAILED:  failures non-empty AND task_ids empty
        PENDING: both empty (no work dispatched yet)

    Args:
        state: The final LangGraph state mapping. Reads ``failures``
            (list[FailureRecord]) and ``task_ids`` (dict[str, str]).

    Returns:
        A dict with these keys:
            status: One of ``SUCCESS`` / ``PARTIAL`` / ``FAILED`` /
                ``PENDING``.
            succeeded_count: ``len(task_ids)``.
            failed_count: ``len(failures)``.
            failures: list of ``{task_label, kind, message}`` dicts;
                use directly — no tuple cast; ``traceback_digest`` stripped
                and ``message`` redacted.
    """
    failures: list[dict[str, Any]] = state.get("failures", []) or []
    task_ids: dict[str, str] = state.get("task_ids", {}) or {}

    succeeded_count = len(task_ids)
    failed_count = len(failures)

    status: _UniversalStatus
    if succeeded_count == 0 and failed_count == 0:
        status = "PENDING"
    elif failed_count == 0:
        status = "SUCCESS"
    elif succeeded_count == 0:
        status = "FAILED"
    else:
        status = "PARTIAL"

    return {
        "status": status,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
        "failures": [
            {
                "task_label": f["task_label"],
                "kind": f["kind"],
                "message": redact_failure_message(f["message"]),
            }
            for f in failures
        ],
    }


def project_interop_metadata(state: Mapping[str, Any]) -> dict[str, Any]:
    """Project safe outbound interop summaries into formatted metadata.

    Only operator target/capability labels, the transport kind, a bounded
    status vocabulary, and a measured latency are exposed. Endpoint URLs,
    credential references, exception text, and protocol correlations remain
    in the sanitized raw/intermediate state (or failure channel) only.
    """
    raw_records = state.get("interop") or []
    records: list[dict[str, Any]] = []
    for record in raw_records:
        if not isinstance(record, Mapping):
            continue
        kind = str(record.get("kind", ""))
        status = str(record.get("status", ""))
        if kind not in {"mcp", "a2a"}:
            continue
        if status not in {"completed", "input_required", "degraded", "failed"}:
            continue
        try:
            latency_ms = float(record.get("latency_ms", 0.0))
        except (TypeError, ValueError):
            latency_ms = 0.0
        if not math.isfinite(latency_ms):
            latency_ms = 0.0
        records.append(
            {
                "target_id": str(record.get("target_id", ""))[:128],
                "kind": kind,
                "capability": str(record.get("capability", ""))[:128],
                "status": status,
                "latency_ms": max(0.0, min(latency_ms, 86_400_000.0)),
            }
        )
    metadata: dict[str, Any] = {}
    if records:
        metadata["interop"] = records
    if bool(state.get("degraded_interop")):
        metadata["degraded_interop"] = True
    return metadata
