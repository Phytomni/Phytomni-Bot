# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Test-only API that fails three archive publications before delegation."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.mcp.formatting.models import ResultArchiveDescriptor
from mcp_server_phytomni.runtime.result_archive import (
    ResultArchiveError,
    ResultArchiveInventory,
)
from mcp_server_phytomni.runtime.run_registry import RunRegistry
from mcp_server_phytomni.runtime.run_registry_delivery import (
    ResultDeliveryDependencies,
    default_result_delivery_dependencies,
)

_REQUIRED_FAILURES = 3
_FAILURE_LOCK = threading.Lock()
_FAILURES_RAISED = 0


def _require_test_environment() -> None:
    """Refuse import unless the live test and temporary DB gates are exact."""
    if os.environ.get("PHYTOMNI_RUN_INTEGRATION") != "1":
        raise RuntimeError("fault injection requires integration opt-in")
    if os.environ.get("PHYTOMNI_E2E_DELIVERY_FAIL_COUNT") != str(
        _REQUIRED_FAILURES
    ):
        raise RuntimeError("fault injection requires exactly three failures")
    raw_db_path = os.environ.get("PHYTOMNI_TASKS_DB", "")
    candidate = Path(raw_db_path).expanduser()
    if not raw_db_path or not candidate.is_absolute():
        raise RuntimeError("fault injection requires an absolute task DB")
    resolved = candidate.resolve()
    temp_root = Path("/tmp").resolve()
    if resolved == temp_root or not resolved.is_relative_to(temp_root):
        raise RuntimeError("fault injection task DB must be below /tmp")


_require_test_environment()
_REAL_DELIVERY = default_result_delivery_dependencies()


def _fail_then_publish(
    inventory: ResultArchiveInventory,
    agent: str,
    summary_markdown: str,
) -> ResultArchiveDescriptor:
    """Raise three retryable failures process-wide, then publish normally."""
    global _FAILURES_RAISED  # pylint: disable=global-statement
    with _FAILURE_LOCK:
        should_fail = _FAILURES_RAISED < _REQUIRED_FAILURES
        if should_fail:
            _FAILURES_RAISED += 1
    if should_fail:
        raise ResultArchiveError("archive_publish_failed", retryable=True)
    return _REAL_DELIVERY.publish(inventory, agent, summary_markdown)


def _registry_factory(db_path: str) -> RunRegistry:
    """Build a registry whose delivery worker uses the injected publisher."""
    return RunRegistry(
        db_path,
        delivery_dependencies=ResultDeliveryDependencies(
            publish=_fail_then_publish,
            sleep=_REAL_DELIVERY.sleep,
        ),
    )


app = create_app(run_registry_factory=_registry_factory)
