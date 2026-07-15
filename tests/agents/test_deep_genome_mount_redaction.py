# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""deep_genome mount-fault failure strings are safe at creation.

Optional analysis mounts redact exception text before writing FailureRecords.
The mandatory BriefGene mount instead aborts with a fixed public error and
logs only the exception class, so no URL, bearer token, or credential
fragment reaches graph state or the public error.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from langgraph.graph.state import CompiledStateGraph

from mcp_server_phytomni.agents.deep_genome.brief_gene_mount import (
    RequiredBriefGeneError,
    make_brief_gene_mount_node,
)
from mcp_server_phytomni.agents.deep_genome.mount_common import (
    degraded_analysis_delta,
)

pytestmark = pytest.mark.agent

_SECRET_EXC = RuntimeError(
    "POST https://internal.host:9000/run?token=deadbeef failed; "
    "Authorization: Bearer abc.def123"
)
_LEAKS = ("deadbeef", "internal.host", "abc.def123", "://")


def _assert_scrubbed(blob: str) -> None:
    """Assert no secret fragment survives and a redaction marker remains."""
    assert isinstance(blob, str)
    for leak in _LEAKS:
        assert leak not in blob, f"unredacted fragment {leak!r} in {blob!r}"
    assert "<redacted" in blob


@pytest.mark.parametrize(
    ("delta", "raw_key"),
    [
        (
            degraded_analysis_delta("evolution_analysis", 2, _SECRET_EXC),
            "task_2",
        ),
        (
            degraded_analysis_delta("digital_design", 3, _SECRET_EXC),
            "task_3",
        ),
    ],
)
def test_mount_delta_redacts_message_and_raw_error(
    delta: dict, raw_key: str
) -> None:
    """Evolution / design mounts scrub both the message and the raw error."""
    _assert_scrubbed(delta["failures"][0]["message"])
    _assert_scrubbed(delta["raw_analyst_data"][raw_key]["error"])


async def test_brief_gene_mount_uses_fixed_public_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The required BriefGene mount never exposes upstream exception text."""

    async def _raising_ainvoke(_input: Any) -> Any:
        raise _SECRET_EXC

    class _BrokenApp:
        ainvoke = staticmethod(_raising_ainvoke)

    mount = make_brief_gene_mount_node(cast(CompiledStateGraph, _BrokenApp()))

    with pytest.raises(
        RequiredBriefGeneError, match="^brief gene profile failed$"
    ):
        await mount({"gene_id": "g1"})

    assert "deadbeef" not in caplog.text
    assert "internal.host" not in caplog.text
    assert "abc.def123" not in caplog.text
