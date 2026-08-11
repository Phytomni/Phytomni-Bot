# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline proof that live outbound-pooling gates fail closed."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from unittest.mock import Mock

import pytest
from e2e.helpers.outbound_pooling import (
    LIVE_GATE_NAMES,
    MISSING_LIVE_GATE_REASON,
    MissingOutboundLiveGateError,
    require_live_gates,
)

pytestmark = pytest.mark.unit


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
