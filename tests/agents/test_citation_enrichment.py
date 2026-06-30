# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the shared bibliographic citation enricher."""

from unittest.mock import AsyncMock, patch

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from mcp_server_phytomni.agents.shared import citation_enrichment
from mcp_server_phytomni.agents.shared.citation_enrichment import (
    enrich_cited_doc_list,
)

_OK = {
    "message": "ok",
    "data": [
        {
            "file_id": "f1",
            "au": "Smith J",
            "ti": "A title",
            "so": "Nature",
            "vl": "11",
            "bp": "1",
            "ep": "9",
            "py": "2020",
            "di": "10.1/x",
            "dl": "https://doi.org/10.1/x",
            "pm": "999",
        }
    ],
}


@pytest.mark.asyncio
async def test_enrich_merges_fields_on_match():
    """Bibliographic fields are merged into a doc whose file_id matches."""
    docs = [{"file_id": "f1", "title": "A title.pdf"}]
    with patch.object(
        citation_enrichment, "bi_query", AsyncMock(return_value=_OK)
    ):
        await enrich_cited_doc_list(docs)
    assert docs[0]["au"] == "Smith J"
    assert docs[0]["pm"] == "999"
    assert docs[0]["title"] == "A title.pdf"  # title untouched here


@pytest.mark.asyncio
async def test_enrich_leaves_unmatched_doc_untouched():
    """A doc whose file_id is absent from the BI result is not modified."""
    docs = [{"file_id": "miss", "title": "T"}]
    with patch.object(
        citation_enrichment, "bi_query", AsyncMock(return_value=_OK)
    ):
        await enrich_cited_doc_list(docs)
    assert docs[0] == {"file_id": "miss", "title": "T"}


@pytest.mark.asyncio
async def test_enrich_skips_bi_when_no_file_ids():
    """bi_query is not called when no doc carries a file_id."""
    docs = [{"title": "no id"}]
    mock = AsyncMock(return_value=_OK)
    with patch.object(citation_enrichment, "bi_query", mock):
        await enrich_cited_doc_list(docs)
    mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_degrades_on_mcp_error():
    """A McpError from bi_query leaves all docs untouched (silent degrade)."""
    docs = [{"file_id": "f1", "title": "T"}]
    boom = AsyncMock(side_effect=McpError(ErrorData(code=-1, message="x")))
    with patch.object(citation_enrichment, "bi_query", boom):
        await enrich_cited_doc_list(docs)
    assert docs[0] == {"file_id": "f1", "title": "T"}
