# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the Review agent's legacy ``arun`` option parser."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.review.arun_options import resolve_arun_options

pytestmark = pytest.mark.agent


def test_resolve_arun_options_accepts_all_trailing_positionals() -> None:
    """The four historical trailing arguments retain their original order."""
    assert resolve_arun_options(([], "thread-1", "en-US", "review"), {}) == (
        [],
        "thread-1",
        "en-US",
        "review",
    )


@pytest.mark.parametrize(
    ("args", "kwargs", "message"),
    [
        (
            ([], None, "en-US", "review", "extra"),
            {},
            "arun accepts at most four trailing arguments",
        ),
        (
            ([],),
            {"obs_file_list": []},
            "arun got multiple values for obs_file_list",
        ),
        (
            (),
            {"unexpected": True},
            "unknown DeepResearchAgent arun arguments: unexpected",
        ),
    ],
)
def test_resolve_arun_options_rejects_invalid_legacy_calls(
    args: tuple[object, ...],
    kwargs: dict[str, object],
    message: str,
) -> None:
    """Invalid combinations fail with stable compatibility diagnostics."""
    with pytest.raises(TypeError, match=message):
        resolve_arun_options(args, kwargs)
