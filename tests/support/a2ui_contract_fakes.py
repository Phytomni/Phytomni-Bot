# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared A2UI contract fragments for HTTP shape tests."""

from __future__ import annotations

from typing import Any

__all__ = ["cancelled_response", "gene_id_form_props"]


def cancelled_response(message: str) -> dict[str, Any]:
    """Build a minimal assistant response for a cancelled action."""
    return {
        "response": {
            "choices": [
                {"message": {"content": message, "follow_up_questions": []}}
            ]
        }
    }


def gene_id_form_props() -> dict[str, Any]:
    """Return the canonical Gene ID form properties."""
    return {
        "title": "Gene ID",
        "fields": [
            {
                "name": "gene_id",
                "label": "Gene ID",
                "type": "text",
                "required": True,
            }
        ],
    }
