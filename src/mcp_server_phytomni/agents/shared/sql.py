# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared SQL escaping helper for BI queries built across agents.

The BI backend has no parameterized-query surface, so this module
provides ``sql_literal`` for callers (deep_genome lookups and the
brief_gene gene retriever today; other domains in the future) that
need to embed an identifier or string value inside a SQL statement
without smuggling extra single quotes — and therefore arbitrary
clauses — through user input.
"""

__all__ = ["sql_literal"]


def sql_literal(value: str) -> str:
    """Return a single-quoted SQL literal with quote-doubling escape.

    BI's SQL dialect treats `''` as an escaped single quote inside a
    string literal, so doubling every embedded quote is the minimal
    transformation that keeps a user-supplied gene id or species code
    from breaking out of its quoted context.

    Args:
        value: Raw input destined for a SQL string literal.

    Returns:
        ``value`` wrapped in single quotes with embedded `'` escaped.
    """
    return "'" + value.replace("'", "''") + "'"
