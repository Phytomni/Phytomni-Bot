# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bibliographic citation enrichment for cited-agent document lists."""

import logging
from collections.abc import MutableMapping, Sequence
from typing import Any

from ...runtime.request_context import current_request_id
from .citation_database import (
    CitationDatabaseLookupError,
    lookup_citation_records,
)
from .citation_metadata import (
    CITATION_RECORD_FIELDS,
    CITATION_STATUS_KEY,
    CITATION_STATUS_LOOKUP_FAILED,
    CITATION_STATUS_MATCHED,
    CITATION_STATUS_MISSING,
)

__all__ = ["CITATION_BIBLIO_FIELDS", "enrich_cited_doc_list"]

logger = logging.getLogger(__name__)

# Import-compatible result-formatting alias.
CITATION_BIBLIO_FIELDS = CITATION_RECORD_FIELDS


async def enrich_cited_doc_list(
    doc_list: Sequence[MutableMapping[str, Any]],
) -> None:
    """Merge bibliographic fields into ``doc_list`` docs in place.

    Args:
        doc_list: Cited agent retrieved documents; each is a mutable
        mapping carrying at least ``file_id``.
    """
    eligible_docs: list[MutableMapping[str, Any]] = []
    file_ids: list[str] = []
    docs_by_id: dict[str, list[MutableMapping[str, Any]]] = {}
    for doc in doc_list:
        if not isinstance(doc, MutableMapping):
            continue
        raw_id = doc.get("file_id")
        file_id = str(raw_id).strip() if raw_id is not None else ""
        if not file_id:
            continue
        eligible_docs.append(doc)
        file_ids.append(file_id)
        docs_by_id.setdefault(file_id, []).append(doc)
    if not file_ids:
        return
    unique_ids = list(dict.fromkeys(file_ids))
    try:
        result = await lookup_citation_records(unique_ids)
    except CitationDatabaseLookupError:
        _mark_status(docs_by_id, CITATION_STATUS_LOOKUP_FAILED)
        logger.warning(
            "citation_metadata_lookup_failed request_id=%s",
            current_request_id() or "unknown",
        )
        return
    for doc in eligible_docs:
        file_id = str(doc["file_id"]).strip()
        record = result.records.get(file_id)
        if record is None:
            doc[CITATION_STATUS_KEY] = CITATION_STATUS_MISSING
            continue
        for field in CITATION_RECORD_FIELDS:
            value = record.get(field)
            if value is not None:
                doc[field] = value
        doc[CITATION_STATUS_KEY] = CITATION_STATUS_MATCHED


def _mark_status(
    docs_by_id: dict[str, list[MutableMapping[str, Any]]], status: str
) -> None:
    """Stamp one private lookup outcome on every eligible live document."""
    for docs in docs_by_id.values():
        for doc in docs:
            doc[CITATION_STATUS_KEY] = status
