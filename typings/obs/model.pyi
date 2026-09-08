# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""The installed Huawei SDK response surface used by boundary regressions."""

from typing import TypedDict, Unpack

class _ResponseOptions(TypedDict, total=False):
    """Only the real SDK keyword fields used by the response regressions."""

    contentLength: int | None
    obs_crc64: str | None

class ResponseWrapper:
    """SDK body reader with Content-Length and optional CRC validation."""

    def __init__(
        self,
        conn: object,
        result: object,
        conn_holder: object,
        /,
        **options: Unpack[_ResponseOptions],
    ) -> None:
        """Declare the positional connection and used SDK keyword options."""
        _ = (conn, result, conn_holder, options)

    def read(self, size: int = ...) -> bytes:
        """Read a bounded byte chunk through the SDK validation wrapper."""
        _ = size
        raise NotImplementedError

    def close(self) -> None:
        """Close or return the response connection through the SDK policy."""
