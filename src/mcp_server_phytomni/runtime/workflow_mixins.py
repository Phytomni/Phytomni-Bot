# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Small shared base for workflow mixin classes.

This module exposes `WorkflowMixinBase`, which gives workflow mixins a common
name and kind surface for diagnostics and tests.
"""


class WorkflowMixinBase:
    """Provide a minimal public surface for workflow mixin classes."""

    def workflow_mixin_name(self) -> str:
        """Return the concrete workflow mixin class name.

        Returns:
            Name of the concrete class that includes the mixin.
        """
        return type(self).__name__

    def workflow_mixin_kind(self) -> str:
        """Return the stable kind label for workflow mixins.

        Returns:
            Constant label identifying the object as a workflow mixin.
        """
        return "workflow"
