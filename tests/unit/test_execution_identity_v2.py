# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Canonical execution identity tests."""

from __future__ import annotations

from uuid import UUID

from mcp_server_phytomni.runtime.execution_identity_v2 import new_execution_id


def test_new_execution_id_uses_one_turn_uuid_shape() -> None:
    """All transports receive the same opaque turn UUID shape."""
    first = new_execution_id()
    second = new_execution_id()

    assert first != second
    assert first.startswith("turn-")
    assert str(UUID(first.removeprefix("turn-"))) == first.removeprefix(
        "turn-"
    )
