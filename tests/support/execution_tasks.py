# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Five-key execution.tasks fixtures for GetRun contract tests."""

from __future__ import annotations

from typing import Any

__all__ = ["execution_child", "protein_promoter_children"]


def execution_child(
    task_id: str,
    *,
    accepted: bool = True,
    status: str = "submitted",
    kind: str,
    error_code: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build one canonical ``execution.tasks`` row."""
    row: dict[str, Any] = {
        "id": task_id,
        "accepted": accepted,
        "status": status,
        "kind": kind,
        "error_code": error_code,
    }
    row.update(extra)
    return row


def protein_promoter_children(
    *,
    accepted_extra: dict[str, Any] | None = None,
    failed_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the shared accepted-protein plus doomed-promoter pair."""
    return [
        execution_child(
            "child-accepted",
            kind="protein_structure_analysis",
            **(accepted_extra or {}),
        ),
        execution_child(
            "child-failed",
            accepted=False,
            status="failed",
            kind="promoter_analysis",
            error_code="input_rejected",
            **(failed_extra or {}),
        ),
    ]
