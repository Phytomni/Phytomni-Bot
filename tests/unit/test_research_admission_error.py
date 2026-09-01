# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""HTTP projection of Research domain admission failures."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.research.input_contracts import (
    research_input_failure,
)
from mcp_server_phytomni.api.agent_runs import _research_admission_error

pytestmark = pytest.mark.unit


def test_admission_error_keeps_domain_code_and_safe_message() -> None:
    """HTTP projection must not replace a specific dataset failure."""
    exc = research_input_failure(
        "research_dataset_not_found",
        "Research dataset metadata could not be verified.",
        http_status_hint=422,
        stage="input_resolution",
    )
    error = _research_admission_error(exc)
    assert error.status_code == 422
    assert error.code == "research_dataset_not_found"
    assert error.message == (
        "Research dataset metadata could not be verified."
    )
    assert error.stage == "input_resolution"


def test_admission_error_falls_back_when_safe_message_missing() -> None:
    """Umbrella copy is only the fallback."""
    error = _research_admission_error(ValueError("internal"))
    assert error.code == "research_input_resolution_failed"
    assert error.message == "Research input resolution failed."
