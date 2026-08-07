# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""One cited-result contract across blocking and streaming surfaces."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.resolver_fakes import post_native_run

from mcp_server_phytomni import server
from mcp_server_phytomni.agents.shared import (
    citation_enrichment,
)
from mcp_server_phytomni.agents.shared import (
    gauss as shared_gauss,
)
from mcp_server_phytomni.agents.shared import (
    sql as shared_sql,
)
from mcp_server_phytomni.agents.shared.citation_database import (
    CitationDatabaseLookupError,
    CitationLookupResult,
)
from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_RECORD_FIELDS,
)
from mcp_server_phytomni.api import app as api_app
from mcp_server_phytomni.api.a2ui_runtime import ReviewExecution
from mcp_server_phytomni.common import relay_client
from mcp_server_phytomni.mcp import app as mcp_app

pytestmark = pytest.mark.server

CITED_SURFACES = (
    ("KnowledgeAgent", "phyto-knowledge", "knowledge"),
    ("ReviewAgent", "phyto-review", "review"),
    ("BriefGeneAgent", "phyto-brief-gene", "brief_gene"),
)

_ARGUMENTS: dict[str, dict[str, Any]] = {
    "knowledge": {"user_query": "claim", "obs_file_list": []},
    "review": {"user_query": "claim", "obs_file_list": []},
    "brief_gene": {"user_query": "AT1G01010"},
}

_EXPECTED_REFERENCE = {
    "file_id": "f1",
    "title": "Retrieval title",
    "au": "Taylor, NL; Millar, AH",
    "ti": "Plant Proteomics",
    "so": "PLANT SCIENCE",
    "vl": "12",
    "bp": "4",
    "ep": "9",
    "py": "2026",
    "di": "10.1000/citation",
    "formatted_citation": (
        "Taylor, N. L. & Millar, A. H. Plant Proteomics. "
        "*PLANT SCIENCE* **12,** 4–9 (2026). "
        "[https://doi.org/10.1000/citation]"
        "(https://doi.org/10.1000/citation)"
    ),
}


def _canned_result() -> dict[str, Any]:
    """Return one detached cited result for every invocation."""
    return {
        "choices": [
            {
                "message": {
                    "content": "Claim[document:1].",
                    "doc_list": [
                        {
                            "file_id": "f1",
                            "title": "Retrieval title.PDF",
                        }
                    ],
                }
            }
        ]
    }


def _install_record(path: Path) -> None:
    """Insert the one canonical record used by the surface matrix."""
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO citation_records (
                file_id, au, ti, so, vl, bp, ep, py, di
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "f1",
                "Taylor, NL; Millar, AH",
                "Plant Proteomics",
                "PLANT SCIENCE",
                "12",
                "4",
                "9",
                "2026",
                "10.1000/citation",
            ),
        )


def _install_bounded_lookup(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
) -> None:
    """Read the configured test artifact without a worker-thread hop."""

    async def lookup(file_ids: Any) -> CitationLookupResult:
        columns = ("file_id", *CITATION_RECORD_FIELDS)
        records: dict[str, dict[str, str | None]] = {}
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            for file_id in dict.fromkeys(file_ids):
                row = connection.execute(
                    f"SELECT {','.join(columns)} FROM citation_records "
                    "WHERE file_id = ?",
                    (file_id,),
                ).fetchone()
                if row is not None:
                    records[str(file_id)] = dict(row)
        return CitationLookupResult(records=records)

    monkeypatch.setattr(citation_enrichment, "lookup_citation_records", lookup)


def _forbid_non_sqlite_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if citation projection reaches any former database seam."""

    async def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("citation enrichment called a non-SQLite seam")

    monkeypatch.setattr(shared_sql, "bi_query", forbidden)
    monkeypatch.setattr(shared_gauss, "gauss_query", forbidden)
    monkeypatch.setattr(relay_client.RelayClient, "post_json", forbidden)


def _install_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    """Install one handler that returns a fresh cited result."""

    async def handler(_arguments: Any) -> dict[str, Any]:
        return deepcopy(_canned_result())

    monkeypatch.setitem(server.TOOL_HANDLERS, tool_name, handler)


async def _terminal_projection(
    tool_name: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Collect public terminal answer, reference, and metadata projections."""
    events = [
        event
        async for event in mcp_app._terminal_graph_events(
            tool_name,
            {"final_response": _canned_result()},
        )
    ]
    answers = [
        event.data["delta"]
        for event in events
        if event.type == "TextMessageContent"
    ]
    references = [
        event.data["value"]["doc_list"]
        for event in events
        if event.type == "Custom" and event.data["name"] == "phyto.references"
    ]
    metadata = [
        event.data["value"]
        for event in events
        if event.type == "Custom" and event.data["name"] == "phyto.metadata"
    ]
    assert len(answers) == len(references) == 1
    assert len(metadata) <= 1
    return answers[0], references[0][0], metadata[0] if metadata else {}


@pytest.mark.parametrize(("tool_name", "model", "slug"), CITED_SURFACES)
async def test_cited_surfaces_share_one_complete_nature_contract(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    model: str,
    slug: str,
) -> None:
    """MCP, OpenAI, native, and graph projections are object-equivalent."""
    _install_record(citation_db_path)
    _install_bounded_lookup(monkeypatch, citation_db_path)
    _forbid_non_sqlite_calls(monkeypatch)
    _install_handler(monkeypatch, tool_name)
    _install_bounded_lookup(monkeypatch, citation_db_path)
    monkeypatch.setenv("PHYTOMNI_A2UI_ENABLED", "0")

    envelope = await mcp_app.invoke_tool_enveloped(tool_name, _ARGUMENTS[slug])
    assert envelope.formatted.answer == "Claim<sup>1</sup>."
    assert dict(envelope.formatted.references[0]) == _EXPECTED_REFERENCE

    review_result = {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
    }
    review_calls = 0

    async def run_review(**_kwargs: Any) -> ReviewExecution:
        nonlocal review_calls
        review_calls += 1
        return ReviewExecution(
            run_id=f"citation-review-{review_calls}",
            status="succeeded",
            result=deepcopy(review_result),
        )

    if slug == "review":
        monkeypatch.setattr(api_app, "_run_review_with_interrupt", run_review)

    chat_response = await asyncio.wait_for(
        chat_completion(
            api_client,
            issued_api_key,
            model=model,
            content=_ARGUMENTS[slug]["user_query"],
        ),
        timeout=5,
    )
    assert chat_response.status_code == 200
    chat_body = chat_response.json()
    assert chat_body["choices"][0]["message"]["content"] == (
        envelope.formatted.answer
    )
    assert "answer" not in chat_body["formatted"]
    assert chat_body["formatted"]["references"] == [_EXPECTED_REFERENCE]

    native_response = await asyncio.wait_for(
        post_native_run(
            api_client,
            issued_api_key,
            slug,
            _ARGUMENTS[slug],
        ),
        timeout=5,
    )
    assert native_response.status_code == 200
    native_formatted = native_response.json()["result"]["formatted"]
    assert native_formatted["answer"] == envelope.formatted.answer
    assert native_formatted["references"] == [_EXPECTED_REFERENCE]

    stream_answer, stream_reference, stream_metadata = (
        await _terminal_projection(tool_name)
    )
    assert stream_answer == envelope.formatted.answer
    assert stream_reference == _EXPECTED_REFERENCE
    assert stream_metadata == {}


@pytest.mark.parametrize(("tool_name", "_model", "slug"), CITED_SURFACES)
@pytest.mark.parametrize("failure_mode", ("missing", "quarantined", "failed"))
async def test_cited_metadata_failures_degrade_blocking_and_stream(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    _model: str,
    slug: str,
    failure_mode: str,
) -> None:
    """Every selected miss remains successful and title-only."""
    _forbid_non_sqlite_calls(monkeypatch)
    _install_handler(monkeypatch, tool_name)
    _install_bounded_lookup(monkeypatch, citation_db_path)
    if failure_mode == "quarantined":
        with sqlite3.connect(citation_db_path) as connection:
            connection.execute(
                """
                INSERT INTO citation_conflicts (
                    file_id, conflict_json
                ) VALUES (?, ?)
                """,
                (
                    "f1",
                    '{"variant_count":2,"source_lines":[1,2],'
                    '"differing_fields":["ti"]}',
                ),
            )
    elif failure_mode == "failed":

        async def fail_lookup(_ids: Any) -> Any:
            raise CitationDatabaseLookupError()

        monkeypatch.setattr(
            citation_enrichment, "lookup_citation_records", fail_lookup
        )

    envelope = await mcp_app.invoke_tool_enveloped(tool_name, _ARGUMENTS[slug])
    expected_reference = {
        "file_id": "f1",
        "title": "Retrieval title",
        "formatted_citation": "Retrieval title",
        "doi_missing": True,
    }
    assert envelope.formatted.answer == "Claim<sup>1</sup>."
    assert envelope.formatted.metadata["citation_metadata_degraded"] is True
    assert dict(envelope.formatted.references[0]) == expected_reference

    stream_answer, stream_reference, stream_metadata = (
        await _terminal_projection(tool_name)
    )
    assert stream_answer == envelope.formatted.answer
    assert stream_reference == expected_reference
    assert stream_metadata == {"citation_metadata_degraded": True}
