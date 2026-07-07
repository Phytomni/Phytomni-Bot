# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""uvicorn launcher for the external HTTP API.

Public functions: main.
"""

from __future__ import annotations

import uvicorn

from ..config.defaults import ApiConfig
from .app import create_app

__all__ = ["main"]


def main() -> None:
    """Run the HTTP API under uvicorn using ApiConfig host/port.

    This launcher is a separate process from the stdio MCP server; the two
    share only the in-process agent wrapper layer within their own runtime.
    """
    config = ApiConfig()
    uvicorn.run(
        create_app(),
        host=config.API_HOST,
        port=config.API_PORT,
        timeout_graceful_shutdown=config.API_GRACEFUL_SHUTDOWN,
    )


if __name__ == "__main__":
    main()
