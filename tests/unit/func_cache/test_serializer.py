# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for func_cache serialization helpers."""

# pylint: disable=missing-function-docstring

import pytest

from mcp_server_phytomni.func_cache.exceptions import SerializationError
from mcp_server_phytomni.func_cache.serializer import dumps, loads

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("compress", [False, True])
def test_dumps_and_loads_round_trip(compress):
    payload = {
        "species": "arabidopsis thaliana",
        "genes": ["AT1G01010", "AT1G01020"],
        "metadata": {"rank": 1, "score": 0.98},
    }

    serialized = dumps(payload, compress=compress)

    assert isinstance(serialized, bytes)
    assert loads(serialized, compress=compress) == payload


def test_loads_wraps_invalid_pickle_payload():
    with pytest.raises(SerializationError, match="Deserialization failed"):
        loads(b"not-a-pickle")


def test_loads_wraps_invalid_compressed_payload():
    with pytest.raises(SerializationError, match="Decompression failed"):
        loads(b"not-zlib-data", compress=True)
