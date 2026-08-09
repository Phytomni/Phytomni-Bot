# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""uvicorn launcher for the external HTTP API.

Public functions: main.
"""

from __future__ import annotations

from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from ..agents.research.input_contracts import ResearchCoordinatorRequest
from ..config.defaults import ApiConfig
from .app import create_app
from .research_input import ResearchAdmissionRequest

__all__ = ["build_app", "main"]


def build_app(
    *,
    research_input_root_request_factory: (
        Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest] | None
    ) = None,
) -> FastAPI:
    """Build the serving app with its explicit Research root seam.

    No repository-owned composition currently supplies the full coordinator
    ports. Omitting the factory deliberately leaves Research HTTP admission
    unavailable instead of accepting a root that cannot execute.
    """
    return create_app(
        research_input_root_request_factory=(
            research_input_root_request_factory
        ),
        research_input_runtime_required=True,
    )


def main() -> None:
    """Run the HTTP API under uvicorn using ApiConfig host/port.

    This launcher is a separate process from the stdio MCP server; the two
    share only the in-process agent wrapper layer within their own runtime.
    """
    config = ApiConfig()
    uvicorn.run(
        build_app(),
        host=config.API_HOST,
        port=config.API_PORT,
        timeout_graceful_shutdown=config.API_GRACEFUL_SHUTDOWN,
    )


if __name__ == "__main__":
    main()
