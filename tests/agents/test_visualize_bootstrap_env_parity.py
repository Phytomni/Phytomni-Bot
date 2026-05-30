# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Parity guard for ``scripts/_visualize_bootstrap.FAKE_ENV``.

The fake env installed by the visualize bootstrap must cover every
endpoint the production deployment validators require, otherwise
``scripts/visualize_agent_graphs.py`` raises ``ValidationError``
under a cold shell while the test suite masks the gap (tests get
their own ``_TEST_ENV`` from ``tests/conftest.py``).
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from mcp_server_phytomni.config.defaults import (
    SERVER_REQUIRED_ENDPOINT_FIELDS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = REPO_ROOT / "scripts" / "_visualize_bootstrap.py"

pytestmark = pytest.mark.agent


def _load_bootstrap_fake_env() -> dict[str, str]:
    """Return ``FAKE_ENV`` without leaking ``_install_fake_env`` writes.

    Snapshots ``os.environ`` before exec_module runs the bootstrap
    (which calls ``_install_fake_env`` at import time) and restores
    it afterwards so a subsequent test does not see the fake env's
    ``setdefault`` keys lingering in the process environment.
    """
    spec = importlib.util.spec_from_file_location(
        "_visualize_bootstrap_under_test", BOOTSTRAP_PATH
    )
    assert spec is not None and spec.loader is not None
    saved_env = dict(os.environ)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        for added_key in set(os.environ) - set(saved_env):
            del os.environ[added_key]
        os.environ.update(saved_env)
    return dict(module.FAKE_ENV)


def test_fake_env_supersets_server_required_endpoints() -> None:
    """``FAKE_ENV`` covers every ``SERVER_REQUIRED_ENDPOINT_FIELDS``.

    A new required ``ServerConfig`` endpoint added to
    ``config/defaults.py`` must also land in ``FAKE_ENV`` so the
    cold-shell visualize invocation keeps working. The test suite
    otherwise hides the gap because ``tests/conftest.py:_TEST_ENV``
    installs the same keys at pytest startup.
    """
    fake_env = _load_bootstrap_fake_env()
    missing = set(SERVER_REQUIRED_ENDPOINT_FIELDS) - set(fake_env.keys())
    assert not missing, (
        "FAKE_ENV missing required ServerConfig endpoints: "
        f"{sorted(missing)}"
    )


def test_fake_env_covers_analyst_app_id() -> None:
    """``APP_ID`` (AnalystConfig field) must be present in ``FAKE_ENV``.

    Analyst-family configs subclass ``AnalystConfig`` which adds
    ``APP_ID`` to the env-required set; ``build_default_registry``
    instantiates those configs at module load and raises
    ``ValidationError`` if ``APP_ID`` is missing.
    """
    fake_env = _load_bootstrap_fake_env()
    assert (
        "APP_ID" in fake_env
    ), "FAKE_ENV must define APP_ID for AnalystConfig instantiation"


def test_fake_env_app_id_parses_as_compute_tier_mapping() -> None:
    """``APP_ID`` value parses as a 3-tier JSON dict.

    ``AnalystConfig.APP_ID`` is typed as ``Dict[str, str]`` with
    ``{"small", "medium", "large"}`` keys; pydantic-settings parses
    the env string as JSON, so a malformed placeholder would itself
    raise ``ValidationError``. Pin the shape to keep the placeholder
    aligned with the production env contract.
    """
    fake_env = _load_bootstrap_fake_env()
    parsed = json.loads(fake_env["APP_ID"])
    assert set(parsed.keys()) == {
        "small",
        "medium",
        "large",
    }, f"APP_ID tiers should be small/medium/large; got {sorted(parsed)}"
