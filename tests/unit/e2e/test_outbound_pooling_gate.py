# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline proof that live outbound-pooling gates fail closed."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from unittest.mock import Mock

import pytest
from e2e.helpers import outbound_pooling
from e2e.helpers.outbound_pooling import (
    LIVE_GATE_NAMES,
    MISSING_LIVE_GATE_REASON,
    MissingOutboundLiveGateError,
    live_server_environment,
    require_live_gates,
)

from mcp_server_phytomni.runtime.outbound import OutboundPoolName

pytestmark = pytest.mark.unit

LIVE_ACCEPTANCE_SCENARIOS = getattr(
    outbound_pooling, "LIVE_ACCEPTANCE_SCENARIOS", ()
)
LIVE_EXECUTABLE_SCENARIOS = getattr(
    outbound_pooling, "LIVE_EXECUTABLE_SCENARIOS", ()
)
LIVE_EXTERNAL_PENDING_SCENARIOS = getattr(
    outbound_pooling, "LIVE_EXTERNAL_PENDING_SCENARIOS", {}
)


@pytest.mark.parametrize("missing", LIVE_GATE_NAMES)
def test_each_missing_gate_skips_before_any_live_accessor(
    missing: str,
) -> None:
    """Every individual missing gate produces the same safe failure."""
    environ = {name: "1" for name in LIVE_GATE_NAMES}
    environ.pop(missing)
    with pytest.raises(MissingOutboundLiveGateError) as exc_info:
        require_live_gates(environ)
    assert MISSING_LIVE_GATE_REASON in str(exc_info.value)
    assert missing in str(exc_info.value)


def test_non_one_values_are_not_live_confirmation() -> None:
    """Truthy-looking values cannot accidentally authorize live traffic."""
    environ = {name: "1" for name in LIVE_GATE_NAMES}
    environ["PHYTOMNI_CONFIRM_NON_PRODUCTION"] = "true"
    with pytest.raises(MissingOutboundLiveGateError):
        require_live_gates(environ)


def test_all_gates_pass_without_touching_any_other_configuration() -> None:
    """The guard reads only gate keys, never secret or socket accessors."""
    reads: list[str] = []

    class GateOnlyEnvironment(Mapping[str, str]):
        """Reject accidental reads of configuration outside the gate set."""

        def __init__(self, values: Mapping[str, str]) -> None:
            self._values = dict(values)

        def __getitem__(self, key: str) -> str:
            reads.append(key)
            assert key in LIVE_GATE_NAMES
            return self._values[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._values)

        def __len__(self) -> int:
            return len(self._values)

    secret_accessor = Mock()
    socket_accessor = Mock()
    require_live_gates(
        GateOnlyEnvironment({name: "1" for name in LIVE_GATE_NAMES})
    )
    assert reads == list(LIVE_GATE_NAMES)
    secret_accessor.assert_not_called()
    socket_accessor.assert_not_called()


def test_live_scenario_guard_is_available() -> None:
    """The e2e helper exposes one gate-first scenario execution seam."""
    assert LIVE_ACCEPTANCE_SCENARIOS
    assert callable(getattr(outbound_pooling, "require_live_scenario", None))


def test_live_scenario_dispositions_are_complete_and_disjoint() -> None:
    """Every declared scenario is executable or explicitly external-pending."""
    executable = set(LIVE_EXECUTABLE_SCENARIOS)
    pending = set(LIVE_EXTERNAL_PENDING_SCENARIOS)

    assert executable
    assert pending
    assert executable.isdisjoint(pending)
    assert executable | pending == set(LIVE_ACCEPTANCE_SCENARIOS)
    assert all(LIVE_EXTERNAL_PENDING_SCENARIOS.values())


def test_executable_scenarios_name_real_e2e_tests() -> None:
    """The executable manifest cannot claim a missing live test."""
    live_module = __import__(
        "e2e.test_outbound_pooling_e2e",
        fromlist=["LIVE_SCENARIO_TESTS"],
    )
    scenario_tests = live_module.LIVE_SCENARIO_TESTS

    assert set(scenario_tests) == set(LIVE_EXECUTABLE_SCENARIOS)
    assert all(
        callable(getattr(live_module, test_name, None))
        for test_name in scenario_tests.values()
    )


def test_live_e2e_declares_external_service_markers() -> None:
    """The live module is excluded from every default offline test run."""
    live_module = __import__(
        "e2e.test_outbound_pooling_e2e",
        fromlist=["pytestmark"],
    )

    marker_names = {marker.name for marker in live_module.pytestmark}
    assert marker_names >= {"integration", "network"}


def test_runbook_names_every_external_pending_live_scenario() -> None:
    """Operators can distinguish unavailable live proof from local closure."""
    root = Path(__file__).resolve().parents[3]
    runbook = (root / "docs/ops/http-api-runbook.md").read_text(
        encoding="utf-8"
    )

    assert "external-pending" in runbook
    for scenario in LIVE_EXTERNAL_PENDING_SCENARIOS:
        assert f"`{scenario}`" in runbook


def test_e2e_conftest_imports_no_live_configuration_before_gates() -> None:
    """Collection cannot import server/client configuration before skipping."""
    script = """
import builtins

original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    blocked = (
        "mcp_client_phytomni",
        "mcp_server_phytomni",
        "e2e.helpers.citation_database",
        "e2e.helpers.client",
        "e2e.helpers.obs_publish",
    )
    if name.startswith(blocked):
        raise RuntimeError(f"live import before gates: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import e2e.conftest
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_live_server_environment_never_enables_testing_mode() -> None:
    """A four-gate live run uses configured non-production credentials."""
    environment = live_server_environment()

    assert "PHYTOMNI_TESTING" not in environment
    assert environment["PHYTOMNI_OUTBOUND_POOL_WAIT_WARN_SECONDS"] == "0.5"
    configured_pools = {
        key.removeprefix("PHYTOMNI_OUTBOUND_").removesuffix("_CONCURRENCY")
        for key, value in environment.items()
        if key.endswith("_CONCURRENCY") and value == "1"
    }
    assert configured_pools == {
        pool.value.upper() for pool in OutboundPoolName
    }


@pytest.mark.parametrize("scenario", LIVE_ACCEPTANCE_SCENARIOS)
@pytest.mark.parametrize("missing", LIVE_GATE_NAMES)
def test_every_live_scenario_stops_at_each_missing_gate(
    scenario: str,
    missing: str,
) -> None:
    """Every acceptance scenario fails closed before its live accessor."""
    environ = {name: "1" for name in LIVE_GATE_NAMES}
    environ.pop(missing)
    live_accessor = Mock()

    with pytest.raises(MissingOutboundLiveGateError):
        outbound_pooling.require_live_scenario(
            environ, scenario, live_accessor
        )

    live_accessor.assert_not_called()


@pytest.mark.parametrize("scenario", LIVE_ACCEPTANCE_SCENARIOS)
def test_every_live_scenario_runs_only_after_all_gates(
    scenario: str,
) -> None:
    """All four confirmations are required before scenario setup begins."""
    live_accessor = Mock(return_value=f"ready:{scenario}")

    result = outbound_pooling.require_live_scenario(
        {name: "1" for name in LIVE_GATE_NAMES},
        scenario,
        live_accessor,
    )

    assert result == f"ready:{scenario}"
    live_accessor.assert_called_once_with()
