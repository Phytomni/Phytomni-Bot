# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""One cited-result contract across blocking and streaming surfaces."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import count
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.resolver_fakes import post_native_run
from tests.support.sqlite import closed_sqlite_connection

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


@dataclass(frozen=True, slots=True)
class _CitedSurfaceCase:
    """One cited agent's tool, model, and native-run identifiers."""

    tool_name: str
    model: str
    slug: str


@dataclass(frozen=True, slots=True)
class _SurfaceTestContext:
    """HTTP and fixture dependencies shared by one surface assertion case."""

    api_client: httpx.AsyncClient
    issued_api_key: str
    chat_completion: Callable[..., Any]
    citation_db_path: Path
    monkeypatch: pytest.MonkeyPatch


CITED_SURFACES = (
    _CitedSurfaceCase("KnowledgeAgent", "phyto-knowledge", "knowledge"),
    _CitedSurfaceCase("ReviewAgent", "phyto-review", "review"),
    _CitedSurfaceCase("BriefGeneAgent", "phyto-brief-gene", "brief_gene"),
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


@pytest.fixture(name="surface_context")
def build_surface_context(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    chat_completion: Callable[..., Any],
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _SurfaceTestContext:
    """Group the complete-surface test's injected dependencies."""
    return _SurfaceTestContext(
        api_client=api_client,
        issued_api_key=issued_api_key,
        chat_completion=chat_completion,
        citation_db_path=citation_db_path,
        monkeypatch=monkeypatch,
    )


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
    with closed_sqlite_connection(path) as connection:
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
        with closed_sqlite_connection(path) as connection:
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


class _TerminalStreamApp:
    """Yield one terminal graph state through the public streaming seam."""

    def __init__(self) -> None:
        """Initialize stream configuration capture."""
        self._config: Mapping[str, Any] | None = None

    def thread_id(self) -> str | None:
        """Return the public stream's configured graph thread identifier."""
        if self._config is None:
            return None
        configurable = self._config.get("configurable") or {}
        return configurable.get("thread_id")

    async def astream(
        self,
        _state: Mapping[str, Any],
        stream_mode: list[str],
        config: Mapping[str, Any] | None = None,
        *,
        subgraphs: bool = False,
    ) -> AsyncIterator[tuple[tuple[str, ...], str, dict[str, Any]]]:
        """Yield the canned final response without external graph work."""
        assert stream_mode == ["custom", "updates", "values"]
        assert config and config["configurable"]["thread_id"]
        assert subgraphs is True
        self._config = config
        yield (
            (),
            "values",
            {"final_response": _canned_result()},
        )


async def _terminal_projection(
    monkeypatch: pytest.MonkeyPatch,
    case: _CitedSurfaceCase,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Collect public terminal answer, reference, and metadata projections."""
    app = _TerminalStreamApp()

    def target(*_args: Any, **_kwargs: Any) -> tuple[Any, dict[str, Any]]:
        return app, {}

    target_name = {
        "knowledge": "knowledge_stream_target",
        "review": "review_stream_target",
        "brief_gene": "brief_gene_stream_seed",
    }[case.slug]
    monkeypatch.setattr(mcp_app, target_name, target)
    run_id = f"citation-stream-{case.slug}"
    events = [
        event
        async for event in mcp_app.invoke_tool_streamed(
            case.tool_name,
            _ARGUMENTS[case.slug],
            run_id=run_id,
            dialogue_id=f"citation-dialogue-{case.slug}",
        )
    ]
    assert app.thread_id() == run_id
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
    assert len(answers) == 1
    assert len(references) <= 1
    assert len(metadata) <= 1
    reference: dict[str, Any] = {}
    if references:
        reference = references[0][0]
    return answers[0], reference, metadata[0] if metadata else {}


async def _complete_blocking_projection(
    context: _SurfaceTestContext,
    case: _CitedSurfaceCase,
) -> Any:
    """Prepare fixtures and assert the canonical blocking projection."""
    _install_record(context.citation_db_path)
    _install_bounded_lookup(context.monkeypatch, context.citation_db_path)
    _forbid_non_sqlite_calls(context.monkeypatch)
    _install_handler(context.monkeypatch, case.tool_name)
    envelope = await mcp_app.invoke_tool_enveloped(
        case.tool_name, _ARGUMENTS[case.slug]
    )
    assert envelope.formatted.answer == "Claim<sup>1</sup>."
    assert dict(envelope.formatted.references[0]) == _EXPECTED_REFERENCE
    return envelope


def _install_review_projection(
    context: _SurfaceTestContext,
    case: _CitedSurfaceCase,
    envelope: Any,
) -> None:
    """Install the Review-only HTTP execution projection when applicable."""
    if case.slug != "review":
        return

    review_result = {
        "formatted": asdict(envelope.formatted),
        "execution": asdict(envelope.execution),
        "raw": envelope.raw,
    }
    review_calls = count(1)

    async def run_review(**_kwargs: Any) -> ReviewExecution:
        return ReviewExecution(
            run_id=f"citation-review-{next(review_calls)}",
            status="succeeded",
            result=deepcopy(review_result),
        )

    context.monkeypatch.setattr(
        api_app, "_execute_review_with_run_id", run_review
    )
    context.monkeypatch.setattr(
        api_app, "_run_review_with_interrupt", run_review
    )


async def _assert_chat_projection(
    context: _SurfaceTestContext,
    case: _CitedSurfaceCase,
    envelope: Any,
) -> None:
    """Assert the OpenAI-compatible HTTP cited projection."""
    response = await asyncio.wait_for(
        context.chat_completion(
            context.api_client,
            context.issued_api_key,
            model=case.model,
            content=_ARGUMENTS[case.slug]["user_query"],
        ),
        timeout=5,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == (
        envelope.formatted.answer
    )
    assert "answer" not in body["formatted"]
    assert body["formatted"]["references"] == [_EXPECTED_REFERENCE]


async def _assert_native_projection(
    context: _SurfaceTestContext,
    case: _CitedSurfaceCase,
    envelope: Any,
) -> None:
    """Assert the canonical native-run cited projection."""
    response = await asyncio.wait_for(
        post_native_run(
            context.api_client,
            context.issued_api_key,
            case.slug,
            _ARGUMENTS[case.slug],
        ),
        timeout=5,
    )
    assert response.status_code == 200
    formatted = response.json()["result"]["formatted"]
    assert formatted["answer"] == envelope.formatted.answer
    assert formatted["references"] == [_EXPECTED_REFERENCE]


async def _assert_stream_projection(
    monkeypatch: pytest.MonkeyPatch,
    case: _CitedSurfaceCase,
    envelope: Any,
) -> None:
    """Assert cited terminal fields through the public stream seam."""
    answer, reference, metadata = await _terminal_projection(monkeypatch, case)
    assert answer == envelope.formatted.answer
    assert reference == _EXPECTED_REFERENCE
    assert metadata == {}


@pytest.mark.parametrize("case", CITED_SURFACES, ids=lambda case: case.slug)
async def test_cited_surfaces_share_one_complete_nature_contract(
    surface_context: _SurfaceTestContext,
    case: _CitedSurfaceCase,
) -> None:
    """MCP, OpenAI, native, and graph projections are object-equivalent."""
    envelope = await _complete_blocking_projection(surface_context, case)
    _install_review_projection(surface_context, case, envelope)
    await _assert_chat_projection(surface_context, case, envelope)
    await _assert_native_projection(surface_context, case, envelope)
    await _assert_stream_projection(
        surface_context.monkeypatch, case, envelope
    )


@pytest.mark.parametrize("case", CITED_SURFACES, ids=lambda case: case.slug)
@pytest.mark.parametrize("failure_mode", ("missing", "quarantined", "failed"))
async def test_cited_metadata_failures_degrade_blocking_and_stream(
    citation_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
    case: _CitedSurfaceCase,
    failure_mode: str,
) -> None:
    """SQLite misses omit silently; lookup failures stay title-only."""
    _ = tasks_db_path
    _forbid_non_sqlite_calls(monkeypatch)
    _install_handler(monkeypatch, case.tool_name)
    _install_bounded_lookup(monkeypatch, citation_db_path)
    if failure_mode == "quarantined":
        with closed_sqlite_connection(citation_db_path) as connection:
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

    envelope = await mcp_app.invoke_tool_enveloped(
        case.tool_name, _ARGUMENTS[case.slug]
    )
    omit_miss = failure_mode in {"missing", "quarantined"}
    (
        stream_answer,
        stream_reference,
        stream_metadata,
    ) = await _terminal_projection(monkeypatch, case)
    assert stream_answer == envelope.formatted.answer
    if omit_miss:
        assert envelope.formatted.answer == "Claim."
        assert envelope.formatted.references == ()
        assert "citation_metadata_degraded" not in envelope.formatted.metadata
        assert stream_reference == {}
        assert stream_metadata == {}
        return

    expected_reference = {
        "file_id": "f1",
        "title": "Retrieval title",
        "formatted_citation": "Retrieval title",
        "doi_missing": True,
    }
    assert envelope.formatted.answer == "Claim<sup>1</sup>."
    assert envelope.formatted.metadata["citation_metadata_degraded"] is True
    assert dict(envelope.formatted.references[0]) == expected_reference
    assert stream_reference == expected_reference
    assert stream_metadata == {"citation_metadata_degraded": True}
