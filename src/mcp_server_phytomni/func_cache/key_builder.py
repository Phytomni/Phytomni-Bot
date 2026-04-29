import inspect
import pickle
import hashlib

from .exceptions import CacheConfigError, SerializationError

PICKLE_PROTOCOL = 5


class KeyBuilder:

    def __init__(self, func, key_params=None):
        self.func_id = f"{func.__module__}.{func.__qualname__}"
        self.sig = inspect.signature(func)
        all_params = list(self.sig.parameters.keys())

        if key_params is None:
            self.key_params = all_params
        else:
            for p in key_params:
                if p not in all_params:
                    raise CacheConfigError(
                        f"Parameter '{p}' does not exist in function {self.func_id}'s signature, "
                        f"available parameters: {all_params}"
                    )
            self.key_params = list(key_params)

    def build_key(self, args, kwargs):
        try:
            bound = self.sig.bind(*args, **kwargs)
            bound.apply_defaults()
            selected = tuple(
                (name, bound.arguments[name]) for name in self.key_params
            )
            raw = pickle.dumps(selected, protocol=PICKLE_PROTOCOL)
            return hashlib.sha256(raw).hexdigest()
        except (CacheConfigError, SerializationError):
            raise
        except Exception as e:
            raise SerializationError(
                f"Failed to build cache key: {e}"
            ) from e
