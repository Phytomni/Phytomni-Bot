# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""deep_genome mount-fault failure strings are redacted at creation.

A subgraph fault feeds ``str(exc)`` into the FailureRecord ``message``
and the failed ``raw_analyst_data`` entry, both of which can ride the
``raw.phytomni_state`` debug envelope when ``PHYTOMNI_DEBUG`` is on.
Each deep_genome mount redacts the exception text at creation so no URL
/ bearer token / credential fragment reaches that envelope; the
unredacted stack stays only in the ``logger.exception`` log line.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.deep_genome.brief_gene_mount import (
    _degraded_mount_delta,
)
from mcp_server_phytomni.agents.deep_genome.design_mount import (
    _degraded_design_delta,
)
from mcp_server_phytomni.agents.deep_genome.evolution_mount import (
    _degraded_evolution_delta,
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
        (_degraded_evolution_delta(2, _SECRET_EXC), "task_2"),
        (_degraded_design_delta(3, _SECRET_EXC), "task_3"),
    ],
)
def test_mount_delta_redacts_message_and_raw_error(
    delta: dict, raw_key: str
) -> None:
    """Evolution / design mounts scrub both the message and the raw error."""
    _assert_scrubbed(delta["failures"][0]["message"])
    _assert_scrubbed(delta["raw_analyst_data"][raw_key]["error"])


def test_brief_gene_mount_delta_redacts_message() -> None:
    """The brief_gene mount scrubs its FailureRecord message."""
    delta = _degraded_mount_delta("g1", _SECRET_EXC)
    _assert_scrubbed(delta["failures"][0]["message"])
