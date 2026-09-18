# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""SDK transport errors constructed with the SDK's own request types."""

from __future__ import annotations

from openai import APIConnectionError, APITimeoutError, DefaultHttpxClient

__all__ = ["openai_connection_error", "openai_timeout_error"]


def openai_connection_error(url: str) -> APIConnectionError:
    """Build a connection error carrying private request and message data."""
    with DefaultHttpxClient() as client:
        return APIConnectionError(
            message=url,
            request=client.build_request("POST", url),
        )


def openai_timeout_error(url: str) -> APITimeoutError:
    """Build a timeout error without retaining an open transport client."""
    with DefaultHttpxClient() as client:
        return APITimeoutError(client.build_request("POST", url))
