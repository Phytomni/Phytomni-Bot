# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Layer-B sanitizer for optional Research child dataset binding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.agents.research.contracts import (
    ResearchGoal,
    ResearchGoalBatch,
)
from mcp_server_phytomni.agents.research.dataset_binding import (
    bound_child_data_list,
    bound_child_grants,
)

pytestmark = pytest.mark.agent

PARENT = {
    "obs://b/a.csv": "table-a",
    "obs://b/b.csv": "table-b",
    "obs://b/c.csv": "table-c",
}
AUTHORITIES = (
    SimpleNamespace(
        dataset_id="dataset_001",
        exact_reference="obs://b/a.csv",
    ),
    SimpleNamespace(
        dataset_id="dataset_002",
        exact_reference="obs://b/b.csv",
    ),
    SimpleNamespace(
        dataset_id="dataset_003",
        exact_reference="obs://b/c.csv",
    ),
)


@pytest.mark.parametrize(
    ("dataset_ids", "expected"),
    [
        (("dataset_002",), {"obs://b/b.csv": "table-b"}),
        (None, dict(PARENT)),
        ((), {}),
        (("nope",), dict(PARENT)),
        ("dataset_002", dict(PARENT)),
    ],
)
def test_bound_child_data_list_subsets_or_fail_opens(
    dataset_ids: object,
    expected: dict[str, str],
) -> None:
    """None/unknown/str fail-open; empty tuple binds no datasets."""
    snapshot = dict(PARENT)

    result = bound_child_data_list(PARENT, AUTHORITIES, dataset_ids)

    assert result == expected
    assert result is not PARENT
    assert snapshot == PARENT


def test_bound_child_data_list_keeps_parent_order() -> None:
    """Duplicates drop; remaining entries keep parent insertion order."""
    result = bound_child_data_list(
        PARENT,
        AUTHORITIES,
        ("dataset_002", "dataset_001", "dataset_002"),
    )

    assert list(result) == ["obs://b/a.csv", "obs://b/b.csv"]
    assert result == {
        "obs://b/a.csv": "table-a",
        "obs://b/b.csv": "table-b",
    }


def test_bound_child_data_list_differs_by_cited_ids() -> None:
    """Two different cited id tuples produce different child maps."""
    first = bound_child_data_list(PARENT, AUTHORITIES, ("dataset_001",))
    second = bound_child_data_list(PARENT, AUTHORITIES, ("dataset_002",))

    assert first != second


GRANTS = (
    {
        "dataset_id": "dataset-001",
        "exact_reference": "obs://b/a.csv",
        "grant_id": "grant-001",
    },
    {
        "dataset_id": "dataset-002",
        "exact_reference": "obs://b/b.csv",
        "grant_id": "grant-002",
    },
)


def test_bound_child_grants_keeps_matching_references() -> None:
    """Grants whose exact_reference is a child data_list key are kept."""
    result = bound_child_grants(GRANTS, {"obs://b/a.csv": "table-a"})

    assert tuple(grant["dataset_id"] for grant in result) == ("dataset-001",)
    assert result[0]["grant_id"] == "grant-001"


def test_bound_child_grants_empty_data_list_drops_all() -> None:
    """An empty child data_list binds no grants."""
    assert bound_child_grants(GRANTS, {}) == ()


def test_bound_child_grants_preserves_parent_order() -> None:
    """Matching grants keep the parent grant order, not data_list order."""
    result = bound_child_grants(
        GRANTS,
        {"obs://b/b.csv": "table-b", "obs://b/a.csv": "table-a"},
    )

    assert tuple(grant["dataset_id"] for grant in result) == (
        "dataset-001",
        "dataset-002",
    )


def test_research_goal_coerces_dataset_ids() -> None:
    """dataset_ids stay optional, unique, and extra-field-forbidden."""
    assert ResearchGoal(goal="g", dataset_ids="x").dataset_ids is None
    goal = ResearchGoal(
        goal="g",
        dataset_ids=[" dataset_002 ", "dataset_002"],
    )
    assert goal.dataset_ids == ("dataset_002",)
    with pytest.raises(ValidationError):
        ResearchGoal.model_validate({"goal": "g", "unexpected": True})


def test_research_goal_batch_fail_opens_non_list_dataset_ids() -> None:
    """A non-list dataset_ids value does not reject the whole batch."""
    batch = ResearchGoalBatch.model_validate(
        [{"goal": "g1", "dataset_ids": "not-a-list"}]
    )

    assert batch.root[0].dataset_ids is None
