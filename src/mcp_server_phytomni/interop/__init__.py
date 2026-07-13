# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Operator-owned outbound interoperability target configuration.

The package exposes immutable target models and a feature-gated registry.
It does not perform discovery, open network connections, or execute stdio
commands; those transport boundaries are layered on in later phases.
"""

from .models import (
    A2ATarget,
    InteropTarget,
    MCPStdioTarget,
    MCPStreamableHttpTarget,
)
from .registry import (
    InteropRegistry,
    InteropRegistryError,
    load_interop_registry,
)

__all__ = [
    "A2ATarget",
    "InteropRegistry",
    "InteropRegistryError",
    "InteropTarget",
    "MCPStdioTarget",
    "MCPStreamableHttpTarget",
    "load_interop_registry",
]
