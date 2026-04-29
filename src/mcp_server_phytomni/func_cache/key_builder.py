# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Key builder for cache key generation using function signature inspection."""

import inspect
import pickle
import hashlib

from .exceptions import CacheConfigError, SerializationError

PICKLE_PROTOCOL = 5


class KeyBuilder:
    """Builds deterministic cache keys from function arguments.

    This class inspects a function's signature and generates a SHA256 hash
    based on the specified parameters, allowing selective caching of
    function results based on a subset of arguments.

    Instance Attributes:
        func_id: Fully qualified function name (module.qualname).
        sig: Inspect signature of the wrapped function.
        key_params: List of parameter names to include in cache key.
    """

    PICKLE_PROTOCOL = 5

    def __init__(self, func, key_params=None):
        """Initialize the key builder with a function and optional param list.

        Args:
            func: The function to build keys for.
            key_params: List of parameter names to include. If None, all
                parameters are used.
        """
        self.func_id = f"{func.__module__}.{func.__qualname__}"
        self.sig = inspect.signature(func)
        all_params = list(self.sig.parameters.keys())

        if key_params is None:
            self.key_params = all_params
        else:
            for p in key_params:
                if p not in all_params:
                    raise CacheConfigError(
                        f"Parameter '{p}' does not exist in "
                        f"function {self.func_id}'s signature, "
                        f"available parameters: {all_params}"
                    )
            self.key_params = list(key_params)

    def build_key(self, args, kwargs):
        """Build a cache key from the given arguments.

        Returns:
            A SHA256 hex digest string representing the cache key.
        """
        try:
            bound = self.sig.bind(*args, **kwargs)
            bound.apply_defaults()
            selected = tuple((name, bound.arguments[name]) for name in self.key_params)
            raw = pickle.dumps(selected, protocol=PICKLE_PROTOCOL)
            return hashlib.sha256(raw).hexdigest()
        except (CacheConfigError, SerializationError):
            raise
        except Exception as e:
            raise SerializationError(f"Failed to build cache key: {e}") from e
