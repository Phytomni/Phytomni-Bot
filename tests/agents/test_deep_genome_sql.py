# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests that deep_genome.sql.sql_literal escapes embedded quotes safely.

The deep_genome BI helpers used to interpolate gene_id and species_code
into SQL via f-strings, which lets a caller-supplied apostrophe break
the literal context and append arbitrary SQL. These tests pin the
escape contract for the new sql_literal helper and confirm the BI
query callers in dispatch and profile now emit the escaped form.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from mcp_server_phytomni.agents.deep_genome import profile as deep_profile
from mcp_server_phytomni.agents.shared.sql import sql_literal

pytestmark = pytest.mark.agent


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AT1G01010", "'AT1G01010'"),
        ("plain-id", "'plain-id'"),
        ("o'malley", "'o''malley'"),
        ("', DROP TABLE id_table; --", "''', DROP TABLE id_table; --'"),
        ("", "''"),
        ("a'b'c", "'a''b''c'"),
    ],
)
def test_sql_literal_doubles_embedded_quotes(
    value: str, expected: str
) -> None:
    """Verify sql_literal wraps the value and doubles every embedded quote.

    Args:
        value: Raw input that may contain SQL-breaking apostrophes.
        expected: Properly escaped single-quoted literal output.
    """
    assert sql_literal(value) == expected


def test_cached_gene_symbol_lookup_uses_sql_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the symbol lookup posts an escaped quote pair to BI.

    The captured SQL must contain ``''`` (the doubled-quote escape) for
    a gene id that includes an apostrophe and must NOT contain the raw
    apostrophe sandwiched between single quotes (which would terminate
    the literal early and inject arbitrary SQL).

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the BI post
            primitive with an in-memory capture.
    """
    captured: Dict[str, Any] = {}

    async def _capture(
        sql: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """Record the SQL and return a minimal symbol payload."""
        del timeout
        captured["sql"] = sql
        return {"data": [{"symbol": "SYM1"}]}

    monkeypatch.setattr(deep_profile, "_post_bi_sql", _capture)
    symbol_lookup = getattr(deep_profile, "_cached_gene_symbol_lookup")

    result = asyncio.run(
        symbol_lookup(
            species_code="ATH",
            gene_id="o'malley",
        )
    )

    assert result == ["SYM1"]
    sql = captured["sql"]
    assert "''" in sql
    assert "'o''malley'" in sql
    assert "'ATH'" in sql


def test_cached_gene_annotation_lookup_uses_sql_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the annotation lookup escapes both gene_id and species_code.

    Args:
        monkeypatch: Pytest monkeypatch fixture used to swap the BI post
            primitive with an in-memory capture.
    """
    captured: List[str] = []

    async def _capture(
        sql: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """Record each annotation SQL and return an empty payload."""
        del timeout
        captured.append(sql)
        return {"data": []}

    monkeypatch.setattr(deep_profile, "_post_bi_sql", _capture)
    annotation_lookup = getattr(deep_profile, "_cached_gene_annotation_lookup")

    asyncio.run(
        annotation_lookup(
            species_code="bad'species",
            gene_id="bad'gene",
        )
    )

    assert len(captured) == 4
    for sql in captured:
        assert "'bad''gene'" in sql
        assert "'bad''species'" in sql
