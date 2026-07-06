# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bibliographic citation enrichment for cited-agent doc lists.

Batch-fetches full bibliographic records (au/ti/so/vl/bp/ep/py/di/dl/pm)
from ``s_rag_reference_citation`` by ``file_id`` and merges them into
each doc in place. Docs with no match are left unchanged; a BI failure
degrades silently so a cited answer never fails on this lookup.
"""

import logging
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from mcp.shared.exceptions import McpError

from ...common.http import JsonPostRetry
from .sql import bi_query, sql_literal

__all__ = ["CITATION_BIBLIO_FIELDS", "enrich_cited_doc_list"]

logger = logging.getLogger(__name__)

_CITATION_TABLE = "s_rag_reference_citation"
#: Bibliographic fields fetched from s_rag_reference_citation and
#: projected onto cited document references.  Exported so that the
#: result-formatting layer can mirror the exact same set without a
#: second definition.
CITATION_BIBLIO_FIELDS: tuple[str, ...] = (
    "au",
    "ti",
    "so",
    "vl",
    "bp",
    "ep",
    "py",
    "di",
    "dl",
    "pm",
)
_CITATION_COLUMNS = CITATION_BIBLIO_FIELDS
_RETRY = JsonPostRetry(
    timeout=30.0,
    max_retries=2,
    retriable_codes=(500, 502, 503, 504),
    message="Failed to query citation metadata",
    network_message="Citation metadata network error",
)


async def enrich_cited_doc_list(
    doc_list: Sequence[MutableMapping[str, Any]],
) -> None:
    """Merge bibliographic fields into ``doc_list`` docs in place.

    Args:
        doc_list: Cited agent retrieved documents; each is a mutable
            mapping carrying at least ``file_id``. Matching docs gain
            au/ti/so/vl/bp/ep/py/di/dl/pm; others are left untouched.
    """
    file_ids = [
        str(doc["file_id"])
        for doc in doc_list
        if isinstance(doc, MutableMapping) and doc.get("file_id")
    ]
    if not file_ids:
        return
    records = await _fetch_citation_records(file_ids)
    if not records:
        return
    for doc in doc_list:
        if not isinstance(doc, MutableMapping):
            continue
        record = records.get(str(doc.get("file_id")))
        if record is None:
            continue
        for column in _CITATION_COLUMNS:
            value = record.get(column)
            if value is not None:
                doc[column] = value


async def _fetch_citation_records(
    file_ids: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    """Return a ``file_id`` -> bibliographic record map.

    Returns an empty map on any BI failure so the caller degrades to
    title-only references instead of raising.
    """
    unique_ids = list(dict.fromkeys(file_ids))
    in_list = ", ".join(sql_literal(fid) for fid in unique_ids)
    columns = ", ".join(("file_id", *_CITATION_COLUMNS))
    sql = (
        f"SELECT {columns} FROM {_CITATION_TABLE} "
        f"WHERE file_id IN ({in_list})"
    )
    try:
        response = await bi_query(sql, retry=_RETRY)
    except McpError:
        logger.warning(
            "citation metadata lookup failed; using title-only refs"
        )
        return {}
    return {
        str(row["file_id"]): row
        for row in _rows(response)
        if isinstance(row, Mapping) and row.get("file_id")
    }


def _rows(response: Any) -> list[Mapping[str, Any]]:
    """Return BI envelope data rows from a ``bi_query`` response."""
    if not isinstance(response, Mapping) or response.get("message") != "ok":
        return []
    data = response.get("data", [])
    return data if isinstance(data, list) else []
