# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IAM token helper for service API authentication."""

from httpx import AsyncClient, HTTPError, Timeout
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ..config.defaults import ServerConfig
from ..config.settings import SensitiveConfig

SERVER_CONFIG = ServerConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()


async def get_token(
    timeout: float = SERVER_CONFIG.TIMEOUT, region: str = SERVER_CONFIG.REGION
) -> str:
    """Obtain an X-Subject-Token for API authentication."""
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        password = SENSITIVE_CONFIG.USER_PASSWORD.get_secret_value()
        data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": SENSITIVE_CONFIG.USER_NAME,
                            "password": password,
                            "domain": {"name": SENSITIVE_CONFIG.DOMAIN_NAME},
                        },
                    },
                },
                "scope": {"project": {"name": region}},
            },
        }
        try:
            response = await client.post(
                SERVER_CONFIG.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                json=data,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.headers["X-Subject-Token"]
        except HTTPError as e:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to get token: {str(e)}",
                )
            ) from e
