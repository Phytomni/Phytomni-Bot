# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for func_cache serialization helpers.

Covers pickle round trips, optional compression, and wrapped errors for
invalid serialized or compressed payloads.
"""

import threading

import pytest

from mcp_server_phytomni.func_cache.exceptions import SerializationError
from mcp_server_phytomni.func_cache.serializer import dumps, loads

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("compress", [False, True])
def test_dumps_and_loads_round_trip(compress):
    """Verify dumps and loads round trip.

    Args:
        compress: Whether to use compressed serialization.
    """
    payload = {
        "species": "arabidopsis thaliana",
        "genes": ["AT1G01010", "AT1G01020"],
        "metadata": {"rank": 1, "score": 0.98},
    }

    serialized = dumps(payload, compress=compress)

    assert isinstance(serialized, bytes)
    assert loads(serialized, compress=compress) == payload


def test_loads_wraps_invalid_pickle_payload():
    """Verify loads wraps invalid pickle payload."""
    with pytest.raises(SerializationError, match="Deserialization failed"):
        loads(b"not-a-pickle")


def test_loads_wraps_invalid_compressed_payload():
    """Verify loads wraps invalid compressed payload."""
    with pytest.raises(SerializationError, match="Decompression failed"):
        loads(b"not-zlib-data", compress=True)


def test_dumps_wraps_unserializable_object_as_serialization_error():
    """Verify dumps surfaces a wrapped SerializationError on failure.

    Pins the symmetric error-wrapping contract: loads already wraps
    decode failures, and dumps must wrap the dump-side branch the same
    way so callers see one exception type for any cache write/read
    failure regardless of which side the corruption is on. A threading
    Lock is a stable unserializable sentinel that triggers the dump
    path's try/except without changing module-level pickling behaviour.
    """
    with pytest.raises(SerializationError, match="Serialization failed"):
        dumps(threading.Lock())
