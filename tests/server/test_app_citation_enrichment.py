# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""The cited-tool enrichment hook in invoke_tool_enveloped."""

from unittest.mock import AsyncMock, patch

import pytest

from mcp_server_phytomni.mcp import app as app_mod
from mcp_server_phytomni.mcp.app import invoke_tool_enveloped


def _cited_payload():
    return {
        "choices": [
            {
                "message": {
                    "content": "Body [1].",
                    "doc_list": [{"file_id": "f1", "title": "T"}],
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_cited_tool_doc_list_is_enriched():
    """Enricher is awaited for cited tools and mutates the live doc dicts."""

    async def fake_enrich(doc_list):
        doc_list[0]["au"] = "Smith J"

    with (
        patch.object(
            app_mod,
            "invoke_tool_raw",
            AsyncMock(return_value=_cited_payload()),
        ),
        patch.object(
            app_mod,
            "enrich_cited_doc_list",
            AsyncMock(side_effect=fake_enrich),
        ),
    ):
        env = await invoke_tool_enveloped("KnowledgeAgent", {})
    assert env.formatted.references[0]["au"] == "Smith J"


@pytest.mark.asyncio
async def test_non_cited_tool_skips_enrichment():
    """Enricher is not called for non-cited tools such as ChatAgent."""
    mock = AsyncMock()
    with (
        patch.object(
            app_mod,
            "invoke_tool_raw",
            AsyncMock(
                return_value={"choices": [{"message": {"content": ""}}]}
            ),
        ),
        patch.object(app_mod, "enrich_cited_doc_list", mock),
    ):
        await invoke_tool_enveloped("ChatAgent", {})
    mock.assert_not_awaited()
