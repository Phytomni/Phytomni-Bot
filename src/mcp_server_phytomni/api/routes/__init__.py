# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Route registration units for the authenticated HTTP API."""

from typing import Any

__all__ = []


def _paging_values(
    created_after: str | None,
    created_before: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Build the shared paging projection for flat query dependencies."""
    return {
        "created_after": created_after,
        "created_before": created_before,
        "limit": limit,
        "offset": offset,
    }
