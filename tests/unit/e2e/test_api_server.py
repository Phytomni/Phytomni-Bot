# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Unit tests for the live API subprocess harness."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from e2e.helpers import api_server

pytestmark = pytest.mark.unit


class _FakeProcess:
    """Process double that exposes only the lifecycle used by the helper."""

    def __init__(self) -> None:
        self.stdout = io.StringIO("")
        self.returncode = None
        self.terminated = False

    def __enter__(self) -> _FakeProcess:
        """Enter the process context."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Leave the process context without external state."""

    def poll(self) -> None:
        """Report a still-running process to the patched health gate."""
        return None

    def terminate(self) -> None:
        """Record orderly teardown."""
        self.terminated = True

    def wait(self, *, timeout: float) -> None:
        """Accept the helper's bounded wait call."""
        del timeout


def test_boot_preserves_explicit_testing_mode(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit test-mode environment reaches the child subprocess."""
    captured: dict[str, str] = {}
    process = _FakeProcess()

    def fake_key_store(_path: str) -> SimpleNamespace:
        """Return a key-store-shaped object for the subprocess fake."""
        return SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(api_key="test-key")
        )

    def fake_popen(
        _cmd: list[str],
        *,
        env: dict[str, str],
        **_kwargs: object,
    ) -> _FakeProcess:
        captured.update(env)
        return process

    monkeypatch.setattr(api_server, "ApiKeyStore", fake_key_store)
    monkeypatch.setattr(api_server, "_free_port", lambda: 43123)
    monkeypatch.setattr(api_server, "_await_healthy", lambda *_args: None)
    monkeypatch.setattr(api_server.subprocess, "Popen", fake_popen)

    with api_server.boot_phytomni_api(
        tmp_path_factory, environment={"PHYTOMNI_TESTING": "1"}
    ) as server:
        assert server.api_key == "test-key"

    assert captured["PHYTOMNI_TESTING"] == "1"
    assert process.terminated
