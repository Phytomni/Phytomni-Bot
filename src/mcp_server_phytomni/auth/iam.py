# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""IAM token helper for service API authentication.

Functions: get_token.
"""

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ..common.http import (
    JsonPostRequest,
    JsonPostRetry,
    request_response_with_retries,
)
from ..config.defaults import ServerConfig
from ..config.settings import SensitiveConfig
from ..runtime.outbound import OutboundPoolName, current_outbound_runtime

SERVER_CONFIG = ServerConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()


async def get_token(
    request_timeout: float | None = None,
    region: str = SERVER_CONFIG.REGION,
) -> str:
    """Obtain an X-Subject-Token for API authentication.

    Transient network errors (``ConnectError`` / timeouts) and retriable
    HTTP status codes are retried with backoff via the shared
    ``request_response_with_retries`` helper, so a single flaky IAM
    connection no longer fails the whole request. This mirrors the
    resilience every downstream BI / NL2SQL call already relies on.

    Args:
        request_timeout: Request timeout in seconds
            (default from ServerConfig.TIMEOUT).
        region: Cloud service region name (default from ServerConfig.REGION).

    Returns:
        str: X-Subject-Token header value for authenticated API requests.

    Raises:
        McpError: If the token request fails after all retries, or a
            successful response omits the X-Subject-Token header.
    """
    timeout = request_timeout
    if timeout is None:
        timeout = SERVER_CONFIG.TIMEOUT
    client = current_outbound_runtime().http.for_pool(OutboundPoolName.IAM)
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
    response = await request_response_with_retries(
        client,
        JsonPostRequest(
            url=SERVER_CONFIG.TOKEN_URL,
            headers={"Content-Type": "application/json"},
            json_body=data,
        ),
        JsonPostRetry(
            timeout=timeout,
            max_retries=SERVER_CONFIG.MAX_RETRIES,
            retriable_codes=SERVER_CONFIG.RETRIABLE_CODES,
            message="Failed to get token",
            network_message="Failed to get token",
        ),
    )
    if response is None:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Failed to get token after all retries",
            )
        )
    token = response.headers.get("X-Subject-Token")
    if not token:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=(
                    "Failed to get token: response missing "
                    "X-Subject-Token header"
                ),
            )
        )
    return token
