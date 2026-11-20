# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Regression tests for pytest-asyncio bootstrap-loop ownership."""

from __future__ import annotations

import asyncio


async def test_async_runner_can_start_without_leaking_bootstrap_loop() -> None:
    """Exercise the first async test that creates pytest's runner loop."""
    await asyncio.sleep(0)


def test_sync_runner_can_follow_the_async_runner() -> None:
    """Exercise the policy transition that used to expose the leaked loop."""
    asyncio.run(asyncio.sleep(0))
