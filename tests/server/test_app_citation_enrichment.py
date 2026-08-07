# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""The cited-tool enrichment hook in invoke_tool_enveloped."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from tests.support.citation_database import install_inline_citation_lookup

from mcp_server_phytomni.agents.shared import sql as shared_sql
from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_STATUS_KEY,
)
from mcp_server_phytomni.mcp import app as app_mod
from mcp_server_phytomni.mcp.app import invoke_tool_enveloped


@pytest.fixture(autouse=True)
def _run_lookup_in_test_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the real synchronous SQLite lookup without an executor thread."""
    install_inline_citation_lookup(monkeypatch)


def _cited_payload():
    return {
        "choices": [
            {
                "message": {
                    "content": "Body [1].",
                    "doc_list": [
                        {
                            "file_id": "f1",
                            "title": "T",
                            "content": "retrieval content sentinel",
                        }
                    ],
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
    assert env.formatted.references[0]["formatted_citation"] == ("Smith J. T.")


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


@pytest.mark.asyncio
async def test_end_to_end_enriched_references_via_sqlite(
    citation_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite rows reach formatted.references without any BI seam."""
    monkeypatch.setenv("PHYTOMNI_RELAY_MODE", "1")
    with sqlite3.connect(citation_db_path) as connection:
        connection.execute(
            "INSERT INTO citation_records (file_id, au, so) VALUES (?, ?, ?)",
            ("f1", "Smith J", "Nature"),
        )

    async def forbidden_database_call(*_args, **_kwargs):
        raise AssertionError(
            "citation enrichment called a non-SQLite database"
        )

    monkeypatch.setattr(shared_sql, "bi_query", forbidden_database_call)
    monkeypatch.setattr(shared_sql, "gauss_query", forbidden_database_call)
    monkeypatch.setattr(shared_sql, "relay_bi_query", forbidden_database_call)
    with (
        patch.object(
            app_mod,
            "invoke_tool_raw",
            AsyncMock(return_value=_cited_payload()),
        ),
    ):
        env = await invoke_tool_enveloped("KnowledgeAgent", {})
    ref = env.formatted.references[0]
    assert ref["file_id"] == "f1"
    assert ref["au"] == "Smith J"
    assert ref["so"] == "Nature"
    assert ref["formatted_citation"] == "Smith J. T. *Nature*."
    assert ref["doi_missing"] is True
    assert CITATION_STATUS_KEY not in ref
    raw_doc = env.raw["choices"][0]["message"]["doc_list"][0]
    assert raw_doc["title"] == "T"
    assert raw_doc["content"] == "retrieval content sentinel"
    assert CITATION_STATUS_KEY not in raw_doc
