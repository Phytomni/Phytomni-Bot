# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared helpers for the deep_genome analysis subgraph mounts.

Holds the degraded-branch delta builder shared by the evolution and
design mounts so the redacted ``FailureRecord`` + failed
``raw_analyst_data`` shape lives in one place rather than duplicated per
mount.
"""

from __future__ import annotations

from typing import Any, Dict

from ..shared.parallel_dispatch import FailureRecord, redact_failure_message


def degraded_analysis_delta(
    analysis_label: str, task_index: Any, exc: BaseException
) -> Dict[str, Any]:
    """Return the analyst-branch delta for a failed analysis mount.

    Shared by the evolution and design mounts: writes a ``FailureRecord``
    to the ``failures`` channel (machine signal) and a failed
    ``raw_analyst_data`` entry under ``analysis_label``, and still
    contributes ``analysis_completed_branches: 1`` so the synthesize
    barrier advances rather than wedging on a transient fault. The
    exception text is redacted at creation (``redact_failure_message``)
    so no URL / token / credential rides the message or raw error into
    the ``raw.phytomni_state`` debug envelope; the unredacted stack stays
    only in ``logger.exception``. ``traceback_digest`` is ``None``: the
    log line already records the full stack.

    Args:
        analysis_label: Analysis-type label used for both the
            ``FailureRecord.task_label`` and the raw entry's
            ``analysis_type`` (e.g. ``"evolution_analysis"`` /
            ``"digital_design"``).
        task_index: Send-payload task index keying the raw entry.
        exc: The caught mount exception.

    Returns:
        The analyst-branch state delta for the failed mount.
    """
    redacted = redact_failure_message(str(exc))
    return {
        "failures": [
            FailureRecord(
                task_label=analysis_label,
                message=redacted,
                kind="execute",
                traceback_digest=None,
            )
        ],
        "raw_analyst_data": {
            f"task_{task_index}": {
                "status": "failed",
                "analysis_type": analysis_label,
                "error": redacted,
            }
        },
        "analysis_completed_branches": 1,
    }
