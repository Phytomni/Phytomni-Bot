# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared duck-typed fakes for AnalystAgent graph-node tests.

The analyst graph-node tests (test_analyst_plan_gate.py and
test_analyst_graph_nodes.py) both build a SimpleNamespace stand-in
for ``SensitiveConfig`` so the bound ``check_node`` / ``plan_node``
calls reach a SecretStr-shaped ``API_KEY``. Centralising the builder
keeps the two files from drifting and keeps pylint's R0801 quiet.
"""

from __future__ import annotations

from types import SimpleNamespace


def fake_analyst_sensitive_config() -> SimpleNamespace:
    """Return a SensitiveConfig stand-in with the three fields nodes read."""
    return SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=lambda: "k"),
        BASE_URL="http://example.invalid",
        MODEL_ID="model",
    )
