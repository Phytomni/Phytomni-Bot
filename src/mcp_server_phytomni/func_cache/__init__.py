# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""SQLite-backed function result caching with TTL and lock helpers.

This package exposes `func_cache`, cache key building, serialization,
storage, lock management, and cache-specific exception classes.
"""

from .decorator import func_cache
from .storage import Storage

__all__ = ["func_cache", "Storage"]
