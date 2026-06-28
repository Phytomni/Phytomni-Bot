# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Unit tests for the GAUSS_DSN GaussDB credential field.

Pins that GAUSS_DSN is a required SecretStr on SensitiveConfig, that a
missing value raises ValidationError in normal mode, and that relay mode
relaxes it (the child Bot relays bi/query and never holds the credential).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from mcp_server_phytomni.config.settings import SensitiveConfig

pytestmark = pytest.mark.unit


def test_gauss_dsn_loads_from_env() -> None:
    """GAUSS_DSN is read from the env populated by conftest._TEST_ENV."""
    cfg = cast(Any, SensitiveConfig)(_env_file=None)
    assert cfg.GAUSS_DSN.get_secret_value().startswith("postgresql://")


def test_gauss_dsn_required_in_normal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing GAUSS_DSN raises ValidationError outside relay mode."""
    monkeypatch.delenv("GAUSS_DSN", raising=False)
    monkeypatch.delenv("RELAY_MODE", raising=False)
    monkeypatch.delenv("PHYTOMNI_RELAY_MODE", raising=False)
    with pytest.raises(ValidationError):
        cast(Any, SensitiveConfig)(_env_file=None)


def test_gauss_dsn_optional_in_relay_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay mode relaxes GAUSS_DSN so a relay child still boots."""
    monkeypatch.delenv("GAUSS_DSN", raising=False)
    monkeypatch.setenv("RELAY_MODE", "1")
    cfg = cast(Any, SensitiveConfig)(_env_file=None)
    assert cfg.GAUSS_DSN.get_secret_value() == ""
