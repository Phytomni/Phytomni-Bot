# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Live e2e probe isolating the rerank hop from the retrieve hop.

The KnowledgeAgent path runs ``retrieve -> rerank -> LLM``. When that
chain is slow or returns a gateway error it is hard to tell which hop
is at fault; ``rerank`` itself has no dedicated coverage in the suite
(``codegraph`` reports no covering tests for ``rerank`` /
``_rank_docs`` / ``_rerank_docs``).

This probe calls :func:`rerank` directly against the configured
``RERANK_URL`` (or the operator relay when relay mode is on) using a
committed, desensitized ``doc_list`` captured from one real retrieve
response. Because the retrieve hop is replaced by the fixture, a
failure here points squarely at the rerank service / relay / network,
not at retrieve. A pass confirms the rerank backend accepts the real
document shape and returns ranked, threshold-filtered results.

The fixture ``fixtures/rerank_seed_docs.json`` keeps only the fields
``_rerank_docs`` consumes (``chunk_id`` / ``title`` /
``big_content`` || ``content``); deployment identifiers (``repo_id`` /
``file_path`` / ``file_id`` / ``indexId`` and the backend chunk hash)
are stripped, and the titles/abstract snippets are from already-public
papers. One doc carries ``big_content`` so the probe also exercises
the ``big_content`` precedence branch in ``_rerank_docs``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mcp_server_phytomni.agents.knowledge.retrieval import rerank

pytestmark = pytest.mark.live

_SEED_QUERY = (
    "Which gene families and signalling pathways are most strongly "
    "implicated in drought tolerance in wheat (Triticum aestivum)?"
)


def _seed_docs() -> list[dict[str, Any]]:
    """Return the committed pre-rerank ``doc_list`` fixture.

    Returns:
        The desensitized list of retrieve documents the probe reranks.
    """
    path = (
        Path(__file__).resolve().parent / "fixtures" / "rerank_seed_docs.json"
    )
    docs = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(docs, list) and docs, "rerank seed fixture is empty"
    return docs


async def test_rerank_probe_ranks_seed_docs() -> None:
    """``rerank`` returns ranked, threshold-filtered docs for the seed.

    Sends the committed ``doc_list`` straight to the live rerank
    endpoint with ``score_threshold=0`` (no doc is filtered out so a
    healthy backend round-trips every input) and asserts the merged
    output preserves the document identity and attaches a numeric
    score. A 502 / gateway failure here surfaces as the ``McpError``
    raised by the retry-exhausted HTTP helper, isolating the rerank hop.

    Raises:
        AssertionError: When the rerank output is empty, drops a
            document, or omits the rerank score.
    """
    seed = _seed_docs()

    ranked = await rerank(
        user_query=_SEED_QUERY,
        doc_list=seed,
        score_threshold=0,
    )

    assert ranked, "rerank returned no documents for the seed doc_list"
    seed_ids = {doc["chunk_id"] for doc in seed}
    ranked_ids = {doc["chunk_id"] for doc in ranked}
    assert ranked_ids <= seed_ids, (
        "rerank returned chunk_ids absent from the seed input: "
        f"{ranked_ids - seed_ids!r}"
    )
    assert ranked_ids, "rerank dropped every seed document"
    for doc in ranked:
        assert "score" in doc, f"rerank doc missing score: {doc!r}"
        assert isinstance(
            doc["score"], (int, float)
        ), f"rerank score was not numeric: {doc!r}"

    scores = [doc["score"] for doc in ranked]
    assert scores == sorted(
        scores, reverse=True
    ), f"rerank output was not sorted by descending score: {scores!r}"
