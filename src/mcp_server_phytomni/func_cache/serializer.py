"""Serialization utilities with optional zlib compression."""

import pickle
import zlib

from .exceptions import SerializationError

PICKLE_PROTOCOL = 5


def dumps(obj, compress=False):
    """Serialize an object to bytes.

    Args:
        obj: The object to serialize.
        compress: Whether to apply zlib compression after pickling.

    Returns:
        Serialized bytes, optionally compressed.

    Raises:
        SerializationError: If serialization fails.
    """
    try:
        data = pickle.dumps(obj, protocol=PICKLE_PROTOCOL)
    except Exception as e:
        raise SerializationError(f"Serialization failed: {e}") from e
    if compress:
        data = zlib.compress(data)
    return data


def loads(data, compress=False):
    """Deserialize bytes back to an object.

    Args:
        data: Serialized bytes to deserialize.
        compress: Whether to decompress before unpickling.

    Returns:
        The deserialized object.

    Raises:
        SerializationError: If deserialization fails.
    """
    if compress:
        try:
            data = zlib.decompress(data)
        except Exception as e:
            raise SerializationError(f"Decompression failed: {e}") from e
    try:
        return pickle.loads(data)
    except Exception as e:
        raise SerializationError(f"Deserialization failed: {e}") from e
