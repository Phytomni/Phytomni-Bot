import pickle
import zlib

from .exceptions import SerializationError

PICKLE_PROTOCOL = 5


def dumps(obj, compress=False):
    try:
        data = pickle.dumps(obj, protocol=PICKLE_PROTOCOL)
    except Exception as e:
        raise SerializationError(f"Serialization failed: {e}") from e
    if compress:
        data = zlib.compress(data)
    return data


def loads(data, compress=False):
    if compress:
        try:
            data = zlib.decompress(data)
        except Exception as e:
            raise SerializationError(f"Decompression failed: {e}") from e
    try:
        return pickle.loads(data)
    except Exception as e:
        raise SerializationError(f"Deserialization failed: {e}") from e
