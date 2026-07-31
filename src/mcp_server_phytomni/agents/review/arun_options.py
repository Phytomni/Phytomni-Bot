# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility parsing for the Review agent's legacy ``arun`` call shape."""

from __future__ import annotations

from typing import Any


def resolve_arun_options(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any, ...]:
    """Resolve legacy positional and keyword options for ``arun``."""
    names = ("obs_file_list", "thread_id", "locale", "review_operation")
    if len(args) > len(names):
        raise TypeError("arun accepts at most four trailing arguments")
    duplicate = next(
        (
            name
            for index, name in enumerate(names)
            if index < len(args) and name in kwargs
        ),
        None,
    )
    if duplicate is not None:
        raise TypeError(f"arun got multiple values for {duplicate}")
    values = tuple(
        kwargs.pop(name, args[index] if index < len(args) else None)
        for index, name in enumerate(names)
    )
    if kwargs:
        raise TypeError(
            "unknown DeepResearchAgent arun arguments: " + next(iter(kwargs))
        )
    return values
