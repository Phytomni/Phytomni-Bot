# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed state and IO contracts for the environment VCI LangGraph.

``EnvironmentInput`` / ``EnvironmentOutput`` / ``EnvironmentState``
split parent-input, parent-output, and internal-state shapes. The
module omits ``from __future__ import annotations`` because
``TypedDict.__required_keys__`` is computed at class-definition
time; lazy annotations would collapse every ``Required[]`` marker
into a ``total=False`` key.
"""

from typing import Any, Required, TypedDict


class EnvironmentInput(TypedDict, total=False):
    """Public input contract for the environment VCI subgraph.

    Mirrors the kwargs ``region_vci_analysis`` accepts: a required
    natural-language region query, a batch flag controlling output
    directory reuse, and a type-erased ``kwargs`` service bag that
    carries chat / submit / OBS overrides flat through to the
    underlying helpers. The bag stays a single dict so parent
    graphs never have to track individual provider kwargs as the
    LLM / OBS surfaces evolve.

    Attributes:
        query: Natural-language region analysis request.
        batch: When ``True`` the caller-provided ``output_dir`` in
            ``kwargs`` is reused instead of materialising a fresh
            run-scoped directory.
        kwargs: Flat dict of chat / submit / OBS overrides forwarded
            to the chat extraction and analyst submission helpers
            (model id, api key, base url, access keys, prompt file,
            environment data, etc.).
    """

    query: Required[str]
    batch: bool
    kwargs: dict[str, Any]


class EnvironmentOutput(TypedDict):
    """Public output contract for the environment VCI subgraph.

    ``vci_analysis_task`` is the submitted analyst task payload
    (``None`` when region-code extraction failed). The key itself
    is always present in the final state so consumers can do a
    single membership check.
    """

    vci_analysis_task: dict[str, Any] | None


class EnvironmentState(TypedDict, total=False):
    """Internal state spanning every environment workflow node.

    Carries every :class:`EnvironmentInput` field plus the
    intermediate ``region_codes`` triple that
    ``extract_region_codes_node`` materialises (``[province,
    city, county]`` or ``None`` on extraction failure) and the
    final ``vci_analysis_task`` matching :class:`EnvironmentOutput`.
    """

    query: Required[str]
    batch: bool
    kwargs: dict[str, Any]
    region_codes: list[str | None] | None
    vci_analysis_task: dict[str, Any] | None
