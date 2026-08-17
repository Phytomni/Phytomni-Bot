# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""DeepGenome profile BI SQL error mapping."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest
from mcp.shared.exceptions import McpError

from mcp_server_phytomni.agents.deep_genome import profile as profile_mod

pytestmark = pytest.mark.unit


async def test_post_bi_sql_maps_non_json_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ValueError from the BI seam becomes a bounded MCP error."""

    async def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValueError("Expecting value")

    monkeypatch.setattr(profile_mod, "bi_query", boom)
    with pytest.raises(McpError) as info:
        await profile_mod._post_bi_sql("SELECT 1")
    assert "non-JSON" in info.value.error.message
