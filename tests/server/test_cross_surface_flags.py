# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Cross-surface feature-flag compatibility matrix.

The optional A2A and memory surfaces must compose without changing the
always-on MCP/native-HTTP/interop contract.  These tests build all four
deployment flag combinations and keep every peer operation offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from mcp_server_phytomni.api.a2a.catalog import build_a2a_skills
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.mcp.app import TOOL_ARGUMENT_MODELS, TOOL_HANDLERS
from mcp_server_phytomni.mcp.schemas import (
    AGENT_TOOL_DEFINITIONS,
    agent_openai_tool_specs,
)

pytestmark = pytest.mark.server

_REAL_ASYNC_REQUEST = httpx.AsyncClient.request

_AGENT_TOOL_NAMES = tuple(
    name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
)
_MCP_TOOL_NAMES = (*_AGENT_TOOL_NAMES, "GetTaskStatus")

_CORE_HTTP_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/v1/models",
        "/v1/chat/completions",
        "/v1/agents",
        "/v1/agents/{agent}/runs",
        "/v1/query/route",
        "/v1/files",
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/logs",
        "/v1/runs/{run_id}/a2ui-actions",
        "/v1/runs/{thread_id}/resume",
        "/v1/conversation-context/settle",
        "/v1/conversation-context/tombstone",
        "/v1/interop/capabilities",
    }
)
_A2A_PATHS = frozenset({"/.well-known/agent-card.json", "/a2a"})
_MEMORY_PATHS = frozenset(
    {
        "/v1/memories",
        "/v1/memories/export",
        "/v1/memories/audit",
        "/v1/memories/{memory_id}",
    }
)


@dataclass(frozen=True, slots=True)
class _SurfaceFlags:
    """One deployment row in the cross-surface feature matrix."""

    a2a_enabled: bool
    memory_enabled: bool


def _flag_cases() -> list[Any]:
    """Return every A2A/memory flag combination."""
    cases: list[Any] = []
    for a2a_enabled in (False, True):
        for memory_enabled in (False, True):
            flags = _SurfaceFlags(
                a2a_enabled=a2a_enabled,
                memory_enabled=memory_enabled,
            )
            bits = "".join(
                "1" if value else "0"
                for value in (a2a_enabled, memory_enabled)
            )
            cases.append(
                pytest.param(
                    flags,
                    id=f"a2a-memory={bits}",
                )
            )
    return cases


def _set_alias(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    """Set one configuration value through its PHYTOMNI alias only."""
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"PHYTOMNI_{name}", raising=False)
    monkeypatch.setenv(f"PHYTOMNI_{name}", value)


def _clear_alias(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Remove both accepted forms of one configuration value."""
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(f"PHYTOMNI_{name}", raising=False)


def _set_split_alias(
    monkeypatch: pytest.MonkeyPatch,
    unprefixed_name: str,
    prefixed_name: str,
    value: str,
) -> None:
    """Set settings whose bare and PHYTOMNI names differ."""
    monkeypatch.delenv(unprefixed_name, raising=False)
    monkeypatch.delenv(prefixed_name, raising=False)
    monkeypatch.setenv(prefixed_name, value)


def _set_flag(
    monkeypatch: pytest.MonkeyPatch, name: str, enabled: bool
) -> None:
    """Set one boolean feature flag through its prefixed alias."""
    _set_alias(monkeypatch, name, "1" if enabled else "0")


def _configure_matrix_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    flags: _SurfaceFlags,
) -> tuple[Path, Path]:
    """Configure one isolated flag-combination test environment."""
    _set_flag(monkeypatch, "A2A_ENABLED", flags.a2a_enabled)
    _set_flag(monkeypatch, "MEMORY_ENABLED", flags.memory_enabled)

    if flags.a2a_enabled:
        _set_alias(
            monkeypatch,
            "A2A_PUBLIC_BASE_URL",
            "https://matrix.example.test/bot",
        )
    else:
        _clear_alias(monkeypatch, "A2A_PUBLIC_BASE_URL")

    _set_alias(monkeypatch, "INTEROP_TARGETS", "[]")

    memory_path = tmp_path / "memory.sqlite"
    _set_alias(monkeypatch, "MEMORY_DB_PATH", str(memory_path))
    _clear_alias(monkeypatch, "API_KEYS_DB_PATH")
    _set_split_alias(
        monkeypatch,
        "API_KEYS_DB_PATH",
        "PHYTOMNI_API_KEYS_DB",
        str(tmp_path / "keys.sqlite"),
    )
    tasks_path = tmp_path / "tasks.sqlite"
    _clear_alias(monkeypatch, "API_TASKS_DB_PATH")
    _set_split_alias(
        monkeypatch,
        "API_TASKS_DB_PATH",
        "PHYTOMNI_TASKS_DB",
        str(tasks_path),
    )
    _set_alias(monkeypatch, "API_SERVICE_TOKEN", "matrix-service-token")
    return memory_path, tasks_path


def _route_paths(app: FastAPI) -> frozenset[str]:
    """Return the registered HTTP path templates for one app instance."""
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
    return frozenset(paths)


def _assert_always_on_catalogs() -> None:
    """Pin the MCP/native HTTP/A2A catalog contract to 0.1.3."""
    mcp_names = tuple(
        name.value for name, _description, _model in AGENT_TOOL_DEFINITIONS
    )
    assert mcp_names == _AGENT_TOOL_NAMES
    assert tuple(TOOL_ARGUMENT_MODELS) == _MCP_TOOL_NAMES
    assert tuple(TOOL_HANDLERS) == _MCP_TOOL_NAMES
    assert (
        tuple(
            str(spec["function"]["name"]) for spec in agent_openai_tool_specs()
        )
        == _AGENT_TOOL_NAMES
    )
    assert tuple(skill.id for skill in build_a2a_skills()) == tuple(
        sorted(_AGENT_TOOL_NAMES)
    )


def _assert_optional_paths(
    paths: frozenset[str], flags: _SurfaceFlags
) -> None:
    """Assert that optional route groups match exactly one matrix row."""
    expected_optional: set[str] = set()
    if flags.a2a_enabled:
        expected_optional.update(_A2A_PATHS)
    if flags.memory_enabled:
        expected_optional.update(_MEMORY_PATHS)
    optional_paths = _A2A_PATHS | _MEMORY_PATHS
    assert (paths & optional_paths) == expected_optional


async def _assert_http_surfaces(
    app: FastAPI,
    key: str,
    flags: _SurfaceFlags,
) -> None:
    """Exercise the HTTP response boundary for one matrix row."""
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://matrix.test",
    ) as client:
        assert (await client.get("/healthz")).status_code == 200

        card = await client.get("/.well-known/agent-card.json")
        assert card.status_code == (200 if flags.a2a_enabled else 404)

        a2a = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "matrix-a2a",
                "method": "GetTask",
                "params": {},
            },
        )
        assert a2a.status_code == (401 if flags.a2a_enabled else 404)

        interop = await client.get("/v1/interop/capabilities", headers=headers)
        assert interop.status_code == 200

        memories = await client.get("/v1/memories", headers=headers)
        assert memories.status_code == (200 if flags.memory_enabled else 404)

        a2ui = await client.post(
            "/v1/runs/matrix-run/a2ui-actions",
            headers=headers,
            json={
                "run_id": "matrix-run",
                "surface_id": "matrix-surface",
                "widget": "confirm",
                "action_id": "matrix-action",
                "payload": {"accepted": True},
            },
        )
        assert a2ui.status_code == 404


@pytest.mark.parametrize(
    "flags",
    _flag_cases(),
)
async def test_all_feature_flag_combinations_preserve_surface_boundaries(
    flags: _SurfaceFlags,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional surfaces compose without changing always-on contracts."""
    memory_path, tasks_path = _configure_matrix_environment(
        monkeypatch,
        tmp_path,
        flags,
    )
    key = (
        ApiKeyStore(str(tmp_path / "keys.sqlite"))
        .create(user_id="matrix-user", scopes=["agents"])
        .api_key
    )
    app = create_app()
    paths = _route_paths(app)

    assert paths >= _CORE_HTTP_PATHS
    _assert_optional_paths(paths, flags)
    assert not memory_path.exists()

    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    await _assert_http_surfaces(app, key, flags)

    if flags.memory_enabled:
        assert memory_path.exists()
    else:
        assert not memory_path.exists()

    _assert_always_on_catalogs()
