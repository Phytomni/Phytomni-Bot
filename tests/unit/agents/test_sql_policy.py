# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the parse-based read-only SQL policy."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.shared.sql_policy import (
    ReadOnlySqlError,
    validate_read_only_sql,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        "SELECT ';' AS literal /* ; inside comment */",
    ],
)
def test_read_only_policy_accepts_safe_queries(sql: str) -> None:
    """Simple PostgreSQL read queries pass the policy."""
    validate_read_only_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "-- comment only",
        "SELECT 1; SELECT 2",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x=1",
        "DELETE FROM t",
        "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN DELETE",
        "CREATE TABLE t(x int)",
        "ALTER TABLE t ADD x int",
        "DROP TABLE t",
        "TRUNCATE t",
        "COPY t TO STDOUT",
        "CALL p()",
        "DO $$ BEGIN END $$",
        "GRANT SELECT ON t TO u",
        "REVOKE SELECT ON t FROM u",
        "SET search_path=x",
        "RESET ALL",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SELECT * INTO new_t FROM old_t",
        "SELECT * FROM t FOR UPDATE",
        "WITH changed AS (DELETE FROM t RETURNING *) SELECT * FROM changed",
    ],
)
def test_read_only_policy_rejects_unsafe_or_unsupported_sql(sql: str) -> None:
    """Writes, control statements, locks, and malformed SQL fail closed."""
    with pytest.raises(
        ReadOnlySqlError, match="one read-only query"
    ) as caught:
        validate_read_only_sql(sql)

    if sql:
        assert sql not in str(caught.value)
