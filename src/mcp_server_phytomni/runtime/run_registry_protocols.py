# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Static callable contracts for run-registry facades."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, Unpack

from .task_manager import Submission


@dataclass(frozen=True, slots=True)
class _ReservedSubmissionRequest:
    """Validated inputs for one reserved-run submission projection."""

    run_id: str
    owner: str
    agent: str
    submissions: Sequence[Submission]
    result: dict[str, Any]
    now: str


class ReservedSubmissionKwargs(TypedDict):
    """Keyword arguments accepted by the reserved-run facade."""

    owner: str
    agent: str
    submissions: Sequence[Submission]
    result: dict[str, Any]
    now: str


class RecordReservedSubmissionsCallable(Protocol):
    """Statically typed public contract for reserved-run projection."""

    def __call__(
        self,
        run_id: str,
        **kwargs: Unpack[ReservedSubmissionKwargs],
    ) -> bool: ...

    @property
    def __name__(self) -> str: ...

    __qualname__: str
