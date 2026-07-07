# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""The run-registry GC runs as a BackgroundTask on the write paths.

Pins that the three sync write routes declare _schedule_run_gc rather
than blocking the response on the SQLite DELETE scan. The listing
route's inline purge and the SSE-finally purge stay out of scope.
"""

# pylint: disable=protected-access
# Test file exercises ``_schedule_run_gc`` (a module-private dependency
# function) directly to assert the four sync write routes declare it.

from __future__ import annotations

import pytest

from mcp_server_phytomni.api import app as api_app

pytestmark = pytest.mark.server


def test_sync_write_routes_declare_gc_dependency() -> None:
    """The three sync write routes carry the _schedule_run_gc dependency."""
    app = api_app.create_app()
    wanted = {
        "/v1/agents/{agent}/runs",
        "/v1/query/route",
        "/v1/chat/completions",
    }
    seen: dict[str, bool] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path in wanted:
            deps = getattr(route, "dependencies", [])
            dep_calls = [d.dependency for d in deps]
            seen[path] = api_app._schedule_run_gc in dep_calls
    assert seen == {p: True for p in wanted}
