# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Branch edges for analyst/task_ops.py HTTP, relay, and probe paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from mcp_server_phytomni.agents.analyst import task_ops as task_ops_module
from mcp_server_phytomni.agents.analyst.defaults import ANALYST_CONFIG
from mcp_server_phytomni.agents.analyst.task_ops import (
    _common_request_kwargs,
    probe_live_status,
    wait_for_completion,
)

pytestmark = pytest.mark.unit


def _patch_http(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    """Stub token + outbound HTTP so wrappers never leave the process."""

    async def fake_token(*, request_timeout: float, region: str) -> str:
        del request_timeout, region
        return "test-token"

    async def fake_request(*_args: Any, **_kwargs: Any) -> Any:
        return response

    monkeypatch.setattr(task_ops_module, "get_token", fake_token)
    monkeypatch.setattr(task_ops_module, "relay_mode_enabled", lambda: False)
    monkeypatch.setattr(
        task_ops_module,
        "current_outbound_http_client",
        lambda _pool: object(),
    )
    monkeypatch.setattr(
        task_ops_module, "request_response_with_retries", fake_request
    )


async def _noop_sleep(_delay: float) -> None:
    """Skip the real poll interval in wait_for_completion tests."""
    return None


def _patch_relay(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    """Route task wrappers through a canned relay client."""

    async def get_json(path: str, **kwargs: Any) -> Any:
        del path, kwargs
        return payload

    async def post_json(path: str, body: Any, **kwargs: Any) -> Any:
        del path, body, kwargs
        return payload

    monkeypatch.setattr(task_ops_module, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(
        task_ops_module,
        "current_relay_client",
        lambda: SimpleNamespace(get_json=get_json, post_json=post_json),
    )


def test_common_request_kwargs_default_and_override_region() -> None:
    """Default region comes from ANALYST_CONFIG; a caller override wins."""
    defaulted = _common_request_kwargs({})
    overridden = _common_request_kwargs({"region": "ap-test-1"})

    assert defaulted["region"] == ANALYST_CONFIG.ANALYSIS_REGION
    assert overridden["region"] == "ap-test-1"
    assert defaulted["retriable_codes"] == list(ANALYST_CONFIG.RETRIABLE_CODES)
    copied = _common_request_kwargs({"retriable_codes": [504]})
    assert copied["retriable_codes"] == [504]


async def test_task_status_returns_json_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 status payload is unwrapped to the parsed JSON body."""
    payload = {"status": "SUCCEEDED"}
    _patch_http(
        monkeypatch,
        SimpleNamespace(status_code=200, json=lambda: payload),
    )

    assert await task_ops_module.task_status("t-ok") == payload


async def test_task_status_raises_when_retries_exhaust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None after retries becomes the documented status McpError."""
    _patch_http(monkeypatch, None)

    with pytest.raises(McpError, match="Failed to check task status"):
        await task_ops_module.task_status("t-none")


async def test_task_status_raises_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surviving non-200 response still fails the wrapper."""
    _patch_http(
        monkeypatch,
        SimpleNamespace(status_code=503, json=lambda: {"err": "busy"}),
    )

    with pytest.raises(McpError, match="Failed to check task status"):
        await task_ops_module.task_status("t-503")


async def test_task_status_uses_relay_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay mode returns the relay JSON without minting an IAM token."""
    _patch_relay(monkeypatch, {"status": "RUNNING"})

    assert await task_ops_module.task_status("t-relay") == {
        "status": "RUNNING"
    }


async def test_probe_live_status_returns_none_for_non_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-mapping live payload is treated as an unknown probe."""

    async def fake_status(task_id: str, **kwargs: Any) -> str:
        del task_id, kwargs
        return "SUCCEEDED"

    monkeypatch.setattr(task_ops_module, "task_status", fake_status)

    assert await probe_live_status("t-str") is None


async def test_probe_live_status_returns_none_for_blank_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dict without a usable status field collapses to None."""

    async def fake_status(task_id: str, **kwargs: Any) -> dict[str, Any]:
        del task_id, kwargs
        return {"status": ""}

    monkeypatch.setattr(task_ops_module, "task_status", fake_status)

    assert await probe_live_status("t-blank") is None


async def test_probe_live_status_swallows_mcp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform McpError is fail-safe None, not a raised error."""

    async def boom(task_id: str, **kwargs: Any) -> dict[str, Any]:
        del task_id, kwargs
        raise McpError(ErrorData(code=INTERNAL_ERROR, message="down"))

    monkeypatch.setattr(task_ops_module, "task_status", boom)

    assert await probe_live_status("t-err") is None


async def test_task_log_relay_and_http_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay log succeeds; a later non-200 HTTP log raises."""
    _patch_relay(monkeypatch, {"log": "ok"})
    assert await task_ops_module.task_log("t-log") == {"log": "ok"}

    _patch_http(
        monkeypatch,
        SimpleNamespace(status_code=404, json=lambda: {}),
    )
    with pytest.raises(McpError, match="Failed to check task log"):
        await task_ops_module.task_log("t-log-miss", compute_resource="medium")


async def test_task_delete_relay_and_http_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay delete returns the success string; HTTP 500 raises."""
    _patch_relay(monkeypatch, {"ok": True})
    assert await task_ops_module.task_delete("t-del") == (
        "Delete task t-del success."
    )

    _patch_http(
        monkeypatch,
        SimpleNamespace(status_code=500, json=lambda: {}),
    )
    with pytest.raises(McpError, match="Failed to delete task"):
        await task_ops_module.task_delete("t-del-fail")


async def test_task_log_and_delete_return_on_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 unwraps task_log JSON and the delete success string."""
    _patch_http(
        monkeypatch,
        SimpleNamespace(status_code=200, json=lambda: {"log": "ok"}),
    )
    assert await task_ops_module.task_log("t-200") == {"log": "ok"}
    assert await task_ops_module.task_delete("t-200") == (
        "Delete task t-200 success."
    )


async def test_wait_for_completion_polls_running_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RUNNING keeps polling; SUCCEEDED returns the payload."""
    statuses = iter(({"status": "RUNNING"}, {"status": "SUCCEEDED"}))

    async def fake_status(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return next(statuses)

    monkeypatch.setattr(task_ops_module, "task_status", fake_status)
    monkeypatch.setattr(task_ops_module.asyncio, "sleep", _noop_sleep)

    result = await wait_for_completion("t-ok", poll_interval=0, max_poll=5)

    assert result == {"status": "SUCCEEDED"}


async def test_wait_for_completion_raises_on_failed_and_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAILED and CANCELLED each raise the matching McpError."""

    async def failed(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "FAILED"}

    monkeypatch.setattr(task_ops_module, "task_status", failed)
    with pytest.raises(McpError, match="Task failed"):
        await wait_for_completion("t-f", poll_interval=0, max_poll=5)

    async def cancelled(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "CANCELLED"}

    monkeypatch.setattr(task_ops_module, "task_status", cancelled)
    with pytest.raises(McpError, match="Task cancelled"):
        await wait_for_completion("t-c", poll_interval=0, max_poll=5)


async def test_wait_for_completion_unknown_status_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown statuses raise McpError; max_poll=0 times out immediately."""

    async def unknown(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "WEIRD"}

    monkeypatch.setattr(task_ops_module, "task_status", unknown)
    with pytest.raises(McpError, match="Task status error"):
        await wait_for_completion("t-u", poll_interval=0, max_poll=5)

    async def running(_task_id: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "RUNNING"}

    monkeypatch.setattr(task_ops_module, "task_status", running)
    with pytest.raises(TimeoutError, match="Exceeded max polling"):
        await wait_for_completion("t-to", poll_interval=0, max_poll=0)
