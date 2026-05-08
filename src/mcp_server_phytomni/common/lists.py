# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Common list helpers."""

from math import ceil
from typing import List

__all__ = [
    "split_list",
]


def split_list(lst: List, max_size: int = 128) -> List[List]:
    """Split a list into evenly sized chunks."""
    n = len(lst)
    if n == 0:
        return []

    num_chunks = ceil(n / max_size)
    base_size = n // num_chunks
    remainder = n % num_chunks

    chunks = []
    index = 0
    for i in range(num_chunks):
        chunk_size = base_size + 1 if i < remainder else base_size
        chunks.append(lst[index : index + chunk_size])
        index += chunk_size
    return chunks
