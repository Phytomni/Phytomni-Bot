# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Customer relay client.

Classes: RelayClient.
Functions: build_relay_client.

Lets a relay-mode child Bot reach the operator's upstream relay API:
builds ``/v1/relay/<path>`` URLs, attaches the bearer key (header only,
never logged), and reuses the shared retry helpers + get_async_client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlencode

from pydantic import SecretStr

from ..config.defaults import ServerConfig
from ..config.settings import SensitiveConfig, get_sensitive_config
from .http import JsonPostRequest, JsonPostRetry, post_json_with_retries
from .httpx_client import get_async_client

__all__ = ["RelayClient", "build_relay_client", "current_relay_client"]

_RELAY_PREFIX = "v1/relay"


@dataclass(frozen=True)
class RelayClient:
    """Bearer-authenticated client for the upstream relay API.

    Attributes:
        base_url: Relay base URL without a trailing slash.
        api_key: Relay bearer key as a ``SecretStr`` so it is masked in
            repr / model dumps and only revealed when the auth header is
            built.
        timeout: Per-request timeout in seconds.
        max_retries: Maximum retry attempts for retriable failures.
        retriable_codes: HTTP status codes that trigger a retry.
    """

    base_url: str
    api_key: SecretStr
    timeout: float
    max_retries: int
    retriable_codes: Tuple[int, ...]

    def relay_url(
        self, relay_path: str, query: Optional[Mapping[str, str]] = None
    ) -> str:
        """Return the absolute relay URL for ``relay_path``.

        Args:
            relay_path: Path under the fixed ``/v1/relay`` prefix, e.g.
                ``"retrieve/search"`` or ``f"analysis/{task_id}"``. A
                leading slash is tolerated.
            query: Optional query parameters to URL-encode onto the URL.

        Returns:
            The fully-qualified relay URL.
        """
        url = f"{self.base_url}/{_RELAY_PREFIX}/{relay_path.lstrip('/')}"
        if query:
            url = f"{url}?{urlencode(dict(query))}"
        return url

    def _auth_headers(
        self, extra: Optional[Mapping[str, str]] = None
    ) -> dict[str, str]:
        """Return the bearer auth header, merged with any extra headers.

        ``extra`` carries business request headers a caller needs the
        relay to forward upstream (e.g. ``X-Workspace-Id`` for NL2SQL);
        the relay strips the caller credential and injects the operator
        one, but forwards other request headers.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}"
        }
        if extra:
            headers.update(extra)
        return headers

    def _retry(self, message: str) -> JsonPostRetry:
        """Return the retry policy with a key-free error prefix."""
        return JsonPostRetry(
            timeout=self.timeout,
            max_retries=self.max_retries,
            retriable_codes=self.retriable_codes,
            message=message,
        )

    async def _request_json(
        self, request: JsonPostRequest, message: str
    ) -> Any:
        """Run one relay request through the shared retry/JSON helper.

        ``post_json_with_retries`` honours ``request.method`` (GET as
        well as POST) and returns the parsed JSON body, raising
        ``McpError`` on non-retriable status or retry exhaustion. The
        ``message`` prefix never contains the relay key, so the key
        stays out of logs and raised errors.
        """
        async with get_async_client(timeout=self.timeout) as client:
            return await post_json_with_retries(
                client, request, self._retry(message)
            )

    async def post_json(
        self,
        relay_path: str,
        *,
        json_body: Any,
        message: str,
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """POST ``json_body`` to a relay route and return parsed JSON.

        ``extra_headers`` carries business request headers the relay
        forwards upstream (e.g. ``X-Workspace-Id`` for NL2SQL).
        """
        request = JsonPostRequest(
            url=self.relay_url(relay_path),
            method="POST",
            headers=self._auth_headers(extra_headers),
            json_body=json_body,
        )
        return await self._request_json(request, message)

    async def get_json(
        self,
        relay_path: str,
        *,
        message: str,
        query: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """GET a relay route (optional allowlisted query) and parse JSON."""
        request = JsonPostRequest(
            url=self.relay_url(relay_path, query),
            method="GET",
            headers=self._auth_headers(),
        )
        return await self._request_json(request, message)


def build_relay_client(
    config: ServerConfig, sensitive: SensitiveConfig
) -> RelayClient:
    """Construct a ``RelayClient`` from the relay config and secret.

    Args:
        config: Any ``ServerConfig`` (or subclass) carrying the relay
            ``RELAY_BASE_URL`` plus the shared timeout / retry policy.
        sensitive: The ``SensitiveConfig`` carrying ``RELAY_API_KEY``.

    Returns:
        A ready ``RelayClient``.
    """
    return RelayClient(
        base_url=config.RELAY_BASE_URL,
        api_key=sensitive.RELAY_API_KEY,
        timeout=config.TIMEOUT,
        max_retries=config.MAX_RETRIES,
        retriable_codes=tuple(config.RETRIABLE_CODES),
    )


def current_relay_client() -> RelayClient:
    """Build a ``RelayClient`` from the live relay config and secret.

    The zero-arg seam for relay-mode HTTP boundaries that have no config
    object in scope (knowledge / data / deep_genome / evolution /
    task-manager adapters). Reads a fresh ``ServerConfig()`` so
    ``RELAY_BASE_URL`` reflects the current environment, and the
    process-cached ``SensitiveConfig`` for ``RELAY_API_KEY``.

    Returns:
        A ``RelayClient`` carrying the live relay base URL, key, and the
        shared timeout / retry policy.
    """
    return build_relay_client(ServerConfig(), get_sensitive_config())
