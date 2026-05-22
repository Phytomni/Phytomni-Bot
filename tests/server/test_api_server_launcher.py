# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the HTTP API uvicorn launcher.

Pins the bridge between ApiConfig and uvicorn.run inside api/server.py
without actually starting a server. Monkeypatches uvicorn.run and
captures the (app, host, port) tuple the launcher forwards.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server_phytomni.api import server as api_launcher

pytestmark = pytest.mark.server


def test_main_forwards_app_host_and_port_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main()`` builds the ASGI app and calls uvicorn with config host/port.

    Pins the launcher contract: ApiConfig.API_HOST and API_PORT must
    reach uvicorn.run unchanged so deployments stay deterministic, and
    the ASGI app must come from create_app() (so the request-context
    middleware and routes register the same way as on stdio launches).
    """
    captured: dict[str, Any] = {}

    def fake_uvicorn_run(app: Any, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(api_launcher.uvicorn, "run", fake_uvicorn_run)

    api_launcher.main()

    assert captured["app"] is not None
    assert isinstance(captured["host"], str)
    assert isinstance(captured["port"], int)
