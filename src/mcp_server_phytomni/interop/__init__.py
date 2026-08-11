# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Operator-owned outbound interoperability configuration and transport.

The package exposes immutable target models, a feature-gated registry, and a
hardened HTTP transport for external peers. Discovery and stdio execution are
layered on in later phases.
"""

from .a2a_client import (
    A2AClientError,
    InteropA2AClientError,
    send_external_a2a,
    send_external_a2a_task,
    stream_external_a2a,
    stream_external_a2a_task,
)
from .a2a_discovery import (
    InteropA2AError,
    discover_external_a2a_capabilities,
    fetch_external_a2a_card,
)
from .a2a_mapping import (
    A2AMappingError,
    ExternalA2AEvent,
    ExternalA2APart,
    build_user_message,
    map_stream_response,
)
from .cache import DiscoveryCache
from .capabilities import (
    DiscoveryError,
    DiscoveryResult,
    InteropCapability,
    InteropCapabilityError,
    discover_external_mcp_capabilities,
    normalize_capabilities,
)
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
from .planner import (
    InteropMode,
    InteropPlan,
    InteropPlanningError,
    InteropTaskSpec,
    plan_interop_capabilities,
)
from .registry import (
    InteropRegistry,
    InteropRegistryError,
    load_interop_registry,
)
from .runtime import (
    InteropResourceRuntime,
    InteropResourceRuntimeError,
    build_interop_resource_runtime,
)
from .security import (
    AsyncDNSResolver,
    EndpointSecurityError,
    ValidatedEndpoint,
    validate_target_request,
)

__all__ = [
    "A2ATarget",
    "A2AClientError",
    "A2AMappingError",
    "AsyncDNSResolver",
    "DiscoveryCache",
    "DiscoveryError",
    "DiscoveryResult",
    "EndpointSecurityError",
    "ExternalA2AEvent",
    "ExternalA2APart",
    "InteropA2AError",
    "InteropA2AClientError",
    "InteropCapability",
    "InteropCapabilityError",
    "InteropMCPError",
    "InteropMCPToolError",
    "InteropMode",
    "InteropPlan",
    "InteropPlanningError",
    "InteropRegistry",
    "InteropRegistryError",
    "InteropResourceRuntime",
    "InteropResourceRuntimeError",
    "InteropTaskSpec",
    "InteropTarget",
    "MCPStdioTarget",
    "MCPStreamableHttpTarget",
    "ValidatedEndpoint",
    "httpx_client_factory",
    "invoke_external_mcp_tool",
    "load_external_mcp_tools",
    "load_interop_registry",
    "build_interop_resource_runtime",
    "discover_external_mcp_capabilities",
    "discover_external_a2a_capabilities",
    "fetch_external_a2a_card",
    "normalize_capabilities",
    "build_user_message",
    "map_stream_response",
    "send_external_a2a",
    "send_external_a2a_task",
    "stream_external_a2a",
    "stream_external_a2a_task",
    "plan_interop_capabilities",
    "validate_target_request",
]
