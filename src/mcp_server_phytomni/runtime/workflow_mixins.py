# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small shared base for workflow mixin classes."""


class WorkflowMixinBase:
    """Provide a minimal public surface for workflow mixin classes."""

    def workflow_mixin_name(self) -> str:
        """Return the concrete workflow mixin class name."""
        return type(self).__name__

    def workflow_mixin_kind(self) -> str:
        """Return the stable kind label for workflow mixins."""
        return "workflow"
