# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Private value types shared by the routing metrics implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class CaseProjection:
    """One classification record after the requested run aggregation."""

    expected_agent: str
    predicted_agent: str
    language: str
    top1_correct: bool
    dispatchable: bool


class AgentValues(TypedDict):
    """Per-agent support and classification rates."""

    support: int
    predicted: int
    true_positive: int
    precision: float
    recall: float
    f1: float


class MajorityValues(TypedDict):
    """Case-majority accuracy values and confidence interval."""

    top1_correct: int
    top1_accuracy: float
    dispatchable_correct: int
    dispatchable_accuracy: float
    wilson_95: list[float]


class ErrorValues(TypedDict):
    """Counts grouped by provider, routing, and schema failure."""

    provider: int
    routing: int
    schema: int


class StabilityValues(TypedDict):
    """Repeat stability rates for one routing case."""

    exact: float
    modal_agreement: float
