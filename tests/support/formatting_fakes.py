# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Payload fragments shared by independent formatting contract tests."""

from __future__ import annotations

from typing import Any

__all__ = [
    "analyst_plan_state",
    "design_task_payload",
    "network_task_payload",
]


def network_task_payload(goal_description: str) -> dict[str, Any]:
    """Build the nested network-task formatter payload."""
    return {
        "network_task": {
            "task_id": "net-1",
            "output_dir": "/obs/phytomni/net/out",
            "compute_resource": "medium",
        },
        "phytomni_state": {"goal_description": goal_description},
    }


def design_task_payload(goal_description: str) -> dict[str, Any]:
    """Build the fan-out design-task formatter payload."""
    return {
        "design_task_result": [
            {
                "task_id": "prot-1",
                "output_dir": "/obs/phytomni/prot",
                "compute_resource": "large",
            },
            {
                "task_id": "prom-1",
                "output_dir": "/obs/phytomni/prom",
                "compute_resource": "large",
            },
        ],
        "phytomni_state": {"goal_description": goal_description},
    }


def analyst_plan_state() -> dict[str, Any]:
    """Build the plan/tool-use metadata fragment."""
    return {
        "plan": "1. retrieve data\n2. analyze\n3. report",
        "plan_feedback": None,
        "plan_retries": 1,
        "extracted_tools": ["pyfasta", "pandas"],
        "tool_usages": "pyfasta -i ...",
        "method_context": {
            "upload": {"path": "sop.pdf"},
            "literature": {"hits": []},
        },
    }
