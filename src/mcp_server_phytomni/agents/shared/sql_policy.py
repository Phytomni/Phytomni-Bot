# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Parse-only policy for SQL sent to the direct GaussDB seam."""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

__all__ = ["ReadOnlySqlError", "validate_read_only_sql"]

_FORBIDDEN_NODE_TYPES: tuple[type[Any], ...] = (
    exp.DDL,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Into,
    exp.Lock,
    exp.Copy,
    exp.Set,
    exp.Grant,
    exp.Revoke,
)
_READ_ONLY_ERROR = "SQL must contain one read-only query"


class ReadOnlySqlError(ValueError):
    """Raised when SQL is not exactly one safe read-only query."""


def validate_read_only_sql(sql: str) -> None:
    """Validate one PostgreSQL read-only query without executing it.

    The parser is called exactly once. Every parse or policy failure uses the
    same fixed message so neither malformed SQL nor a backend payload can
    cross the public error boundary.

    Args:
        sql: Candidate PostgreSQL statement.

    Raises:
        ReadOnlySqlError: If parsing fails, multiple statements are supplied,
            the root is not a query, or a forbidden node is nested anywhere
            in the expression tree.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ReadOnlySqlError(_READ_ONLY_ERROR)
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as exc:
        raise ReadOnlySqlError(_READ_ONLY_ERROR) from exc
    if len(statements) != 1:
        raise ReadOnlySqlError(_READ_ONLY_ERROR)
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise ReadOnlySqlError(_READ_ONLY_ERROR)
    if any(
        isinstance(node, _FORBIDDEN_NODE_TYPES) for node in statement.walk()
    ):
        raise ReadOnlySqlError(_READ_ONLY_ERROR)
