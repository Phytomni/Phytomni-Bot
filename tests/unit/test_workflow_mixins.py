# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the shared WorkflowMixinBase diagnostic surface.

Pins the two public methods every agent mixin inherits so a renamed or
removed method surfaces here instead of through silent diagnostic loss.
"""

from __future__ import annotations

import pytest

from mcp_server_phytomni.runtime.workflow_mixins import WorkflowMixinBase

pytestmark = pytest.mark.unit


class _DemoMixin(WorkflowMixinBase):
    """Minimal concrete class for exercising the base diagnostic methods."""


def test_workflow_mixin_name_returns_concrete_class_name() -> None:
    """``workflow_mixin_name`` returns the concrete subclass name."""
    assert _DemoMixin().workflow_mixin_name() == "_DemoMixin"


def test_workflow_mixin_kind_returns_workflow_label() -> None:
    """``workflow_mixin_kind`` returns the stable ``workflow`` constant."""
    assert _DemoMixin().workflow_mixin_kind() == "workflow"
