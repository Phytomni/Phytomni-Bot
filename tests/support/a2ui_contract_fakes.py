# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared A2UI contract fragments for HTTP shape tests."""

from __future__ import annotations

from typing import Any, TypedDict, Unpack

import httpx

__all__ = [
    "cancelled_response",
    "chat_terminal_state",
    "confirm_surface",
    "gene_id_form_props",
    "post_a2ui_action",
]


class _A2UIActionFields(TypedDict):
    """Keyword fields in one A2UI action request fixture."""

    run_id: str
    surface_id: str
    widget: str
    action_id: str
    payload: dict[str, Any]


def confirm_surface(
    surface_id: str,
    *,
    title: str = "Confirm",
    body: str = "Proceed?",
) -> dict[str, Any]:
    """Build the shared v1.0 confirm surface used by HTTP fixtures."""
    return {
        "catalog_version": "v1.0",
        "surface_id": surface_id,
        "widget": "confirm",
        "props": {"title": title, "body": body},
    }


def chat_terminal_state() -> dict[str, Any]:
    """Build the smallest terminal Chat graph state for resume tests."""
    return {
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "done",
                        "follow_up_questions": [],
                    }
                }
            ]
        }
    }


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


async def post_a2ui_action(
    client: httpx.AsyncClient,
    api_key: str,
    **action: Unpack[_A2UIActionFields],
) -> httpx.Response:
    """Post one authenticated A2UI action envelope."""
    return await client.post(
        f"/v1/runs/{action['run_id']}/a2ui-actions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=dict(action),
    )
