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

from collections.abc import Mapping
from typing import Any, Literal

from ..agents.shared.parallel_dispatch import (
    degraded_labels,
    redact_failure_message,
)

__all__ = [
    "project_degraded_metadata",
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


def project_degraded_metadata(state: Mapping[str, Any]) -> dict[str, Any]:
    """Project ``literature_degraded`` into a status-independent metadata key.

    Reads only the ``literature_degraded`` channel and never ``failures``,
    so it cannot affect the PARTIAL/FAILED status computed by
    :func:`project_universal_failure_metadata`. Returns ``{}`` when there
    is no degradation, so non-degraded and non-brief_gene cited agents
    stay at empty metadata.
    """
    records = state.get("literature_degraded") or []
    if not records:
        return {}
    return {
        "degraded": {
            "reason": "literature_retrieval",
            "count": len(records),
            "labels": degraded_labels(records),
        }
    }
