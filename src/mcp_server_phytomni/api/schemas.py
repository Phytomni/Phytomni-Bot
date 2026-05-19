# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP API request and response schemas.

Public models: ApiErrorDetail, ApiErrorResponse.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

__all__ = ["ApiErrorDetail", "ApiErrorResponse"]


class ApiErrorDetail(BaseModel):
    """One error description inside the unified API error envelope.

    Attributes:
        type: Stable machine-readable error category slug.
        code: HTTP status code mirrored into the body.
        message: Human-readable explanation.
        request_id: Correlation id; populated once request context lands.
    """

    type: str
    code: int
    message: str
    request_id: Optional[str] = None


class ApiErrorResponse(BaseModel):
    """Unified error envelope for native and agents/runs routes.

    Attributes:
        error: The single error detail object.
    """

    error: ApiErrorDetail
