# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Regression contracts for shared execution-store construction."""

from __future__ import annotations

from inspect import Parameter, signature

import pytest

from mcp_server_phytomni.runtime.execution_journal_store_v2 import (
    SQLiteExecutionJournal,
)
from mcp_server_phytomni.runtime.execution_target_store_v2 import (
    SQLiteExecutionTargetStore,
)


@pytest.mark.parametrize("invalid_token", ("", "x" * 129))
def test_fenced_stores_reject_invalid_provider_join_token(
    invalid_token: str,
) -> None:
    """Keep the shared fence-token bounds identical across both stores."""
    for store_type in (SQLiteExecutionJournal, SQLiteExecutionTargetStore):
        with pytest.raises(
            ValueError, match="invalid provider join lease token"
        ):
            store_type(
                "unused.db",
                expected_provider_join_lease_token=invalid_token,
            )


def test_provider_trace_batch_retains_keyword_only_contract() -> None:
    """Keep the pre-refactor public provider-trace call shape visible."""
    parameters = signature(
        SQLiteExecutionJournal.append_provider_trace_batch
    ).parameters
    assert tuple(parameters) == (
        "self",
        "execution_id",
        "owner",
        "work_unit_id",
        "expected_work_revision",
        "cursor",
        "source_revision",
        "adapter_version",
        "overlap_identities",
        "contact_at",
        "health",
        "intents",
    )
    assert parameters["execution_id"].kind is Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameter.kind is Parameter.KEYWORD_ONLY
        for parameter in tuple(parameters.values())[2:]
    )
