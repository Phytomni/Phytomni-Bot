# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for list chunking utilities."""

# pylint: disable=missing-function-docstring

import pytest

from mcp_server_phytomni.utils import split_list

pytestmark = pytest.mark.unit


def test_split_list_preserves_order_and_respects_max_size():
    values = list(range(10))

    chunks = split_list(values, max_size=4)

    assert chunks == [[0, 1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert [item for chunk in chunks for item in chunk] == values
    assert all(len(chunk) <= 4 for chunk in chunks)


def test_split_list_handles_empty_input():
    assert not split_list([])


def test_split_list_uses_single_chunk_when_input_fits():
    assert split_list(["ath", "osa"], max_size=128) == [["ath", "osa"]]
