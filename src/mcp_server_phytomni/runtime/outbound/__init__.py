# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Logical process-local outbound request pool primitives."""

from .http import (
    BoundAsyncRequestClient,
    OutboundHttpFactories,
    OutboundHttpProfile,
    OutboundHttpRuntime,
    build_outbound_http_runtime,
)
from .lifecycle import (
    OutboundResourceFactories,
    OutboundRuntime,
    OutboundRuntimeStateError,
    aclose_outbound_runtime,
    current_obs_runtime,
    current_outbound_http_client,
    current_outbound_runtime,
    init_outbound_runtime,
)
from .models import (
    OutboundPoolName,
    OutboundPoolSnapshot,
    OutboundRuntimeClosedError,
)
from .obs import (
    ObsClientFactory,
    ObsClientRuntime,
    ObsProfileName,
)
from .registry import OutboundPoolRegistry

__all__ = [
    "BoundAsyncRequestClient",
    "OutboundHttpFactories",
    "OutboundHttpProfile",
    "OutboundHttpRuntime",
    "OutboundPoolName",
    "OutboundPoolRegistry",
    "OutboundPoolSnapshot",
    "OutboundResourceFactories",
    "OutboundRuntime",
    "OutboundRuntimeClosedError",
    "OutboundRuntimeStateError",
    "ObsClientFactory",
    "ObsClientRuntime",
    "ObsProfileName",
    "aclose_outbound_runtime",
    "build_outbound_http_runtime",
    "current_obs_runtime",
    "current_outbound_http_client",
    "current_outbound_runtime",
    "init_outbound_runtime",
]
