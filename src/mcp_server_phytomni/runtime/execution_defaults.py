# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Constructors for the canonical empty execution projection."""

from __future__ import annotations

from typing import Any


def empty_execution_projection(
    *, degraded: bool = False, result_archive_required: bool = False
) -> dict[str, Any]:
    """Return an empty formatted/execution envelope for a live run."""
    execution: dict[str, Any] = {
        "tracking": {"degraded": degraded},
        "warnings": [],
        "tasks": [],
        "artifacts": [],
        "output_dirs": [],
        "report": None,
        "diagnostics": [],
    }
    if result_archive_required:
        execution["delivery"] = {
            "schema_version": 1,
            "required": True,
            "status": "pending",
            "revision": 1,
            "inventory_digest": "",
            "archive": None,
            "error_code": None,
            "retryable": False,
        }
    return {
        "formatted": {
            "answer": "",
            "follow_up_questions": [],
            "references": [],
            "tabular": {},
            "metadata": {},
        },
        "execution": execution,
    }


__all__ = ["empty_execution_projection"]
