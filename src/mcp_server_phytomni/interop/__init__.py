# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Operator-owned outbound interoperability configuration and transport.

The package exposes immutable target models, a feature-gated registry, and a
hardened HTTP transport for external peers. Discovery and stdio execution are
layered on in later phases.
"""

from .http_transport import httpx_client_factory
from .mcp_client import (
    InteropMCPError,
    InteropMCPToolError,
    invoke_external_mcp_tool,
    load_external_mcp_tools,
)
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
from .security import (
    AsyncDNSResolver,
    EndpointSecurityError,
    ValidatedEndpoint,
    validate_target_request,
)

__all__ = [
    "A2ATarget",
    "AsyncDNSResolver",
    "EndpointSecurityError",
    "InteropMCPError",
    "InteropMCPToolError",
    "InteropRegistry",
    "InteropRegistryError",
    "InteropTarget",
    "MCPStdioTarget",
    "MCPStreamableHttpTarget",
    "ValidatedEndpoint",
    "httpx_client_factory",
    "invoke_external_mcp_tool",
    "load_external_mcp_tools",
    "load_interop_registry",
    "validate_target_request",
]
