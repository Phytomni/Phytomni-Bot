# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Offline unit tests for the shared supported-species catalog."""

from __future__ import annotations

import logging

import pytest

from mcp_server_phytomni.agents.shared.species_catalog import (
    supported_species_codes,
    warn_if_unsupported_species,
)

pytestmark = pytest.mark.unit

_UNSUPPORTED_FRAGMENT = "not in the supported data map"


def test_supported_species_codes_contains_known_codes() -> None:
    """The catalog exposes the three-letter codes the resolver prompts cite."""
    codes = supported_species_codes()
    assert {"osa", "ath", "zma"} <= codes
    assert "zzz" not in codes


def test_warn_if_unsupported_species_is_silent_for_supported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A supported code logs nothing so the resolver proceeds quietly."""
    logger = logging.getLogger("test.species_catalog.supported")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        warn_if_unsupported_species(logger, "osa", "rice plant height")
    assert _UNSUPPORTED_FRAGMENT not in caplog.text


def test_warn_if_unsupported_species_warns_for_unsupported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unsupported code warns with code and query, without raising."""
    logger = logging.getLogger("test.species_catalog.unsupported")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        warn_if_unsupported_species(logger, "zzz", "an obscure organism")
    assert "zzz" in caplog.text
    assert "an obscure organism" in caplog.text
    assert _UNSUPPORTED_FRAGMENT in caplog.text
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
