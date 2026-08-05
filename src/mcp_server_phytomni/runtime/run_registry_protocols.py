# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Static callable contracts for run-registry facades."""

from __future__ import annotations

import inspect
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


def _reserved_parameter(
    name: str,
    kind: Any,
    annotation: object = inspect.Parameter.empty,
) -> inspect.Parameter:
    """Build one parameter for the reserved-submission facade signature."""
    return inspect.Parameter(name, kind, annotation=annotation)


_RECORD_RESERVED_SUBMISSIONS_SIGNATURE = inspect.Signature(
    parameters=(
        _reserved_parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        _reserved_parameter(
            "run_id", inspect.Parameter.POSITIONAL_OR_KEYWORD, "str"
        ),
        _reserved_parameter("owner", inspect.Parameter.KEYWORD_ONLY, "str"),
        _reserved_parameter("agent", inspect.Parameter.KEYWORD_ONLY, "str"),
        _reserved_parameter(
            "submissions",
            inspect.Parameter.KEYWORD_ONLY,
            "Sequence[Submission]",
        ),
        _reserved_parameter(
            "result", inspect.Parameter.KEYWORD_ONLY, "dict[str, Any]"
        ),
        _reserved_parameter("now", inspect.Parameter.KEYWORD_ONLY, "str"),
    ),
    return_annotation="bool",
)
_RECORD_RESERVED_SUBMISSIONS_ANNOTATIONS: dict[str, object] = {
    "run_id": "str",
    "owner": "str",
    "agent": "str",
    "submissions": "Sequence[Submission]",
    "result": "dict[str, Any]",
    "now": "str",
    "return": "bool",
}


def install_record_reserved_submissions_facade(
    registry_type: type[Any],
) -> None:
    """Install the historical explicit signature on a registry class."""

    def facade(self: Any, *args: Any, **kwargs: Any) -> bool:
        """Adapt public arguments to the typed internal request object."""
        bound = _RECORD_RESERVED_SUBMISSIONS_SIGNATURE.bind(
            self, *args, **kwargs
        )
        request = _ReservedSubmissionRequest(
            run_id=bound.arguments["run_id"],
            owner=bound.arguments["owner"],
            agent=bound.arguments["agent"],
            submissions=bound.arguments["submissions"],
            result=bound.arguments["result"],
            now=bound.arguments["now"],
        )
        implementation = getattr(self, "_record_reserved_submissions")
        return bool(implementation(request))

    metadata = (
        ("__signature__", _RECORD_RESERVED_SUBMISSIONS_SIGNATURE),
        ("__annotations__", _RECORD_RESERVED_SUBMISSIONS_ANNOTATIONS),
        ("__name__", "record_reserved_submissions"),
        (
            "__qualname__",
            f"{registry_type.__name__}.record_reserved_submissions",
        ),
        ("__module__", registry_type.__module__),
        (
            "__doc__",
            getattr(registry_type, "_record_reserved_submissions").__doc__,
        ),
    )
    for name, value in metadata:
        setattr(facade, name, value)
    setattr(registry_type, "record_reserved_submissions", facade)


_SETTLE_RUN_SIGNATURE = inspect.Signature(
    parameters=(
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("run_id", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("owner", inspect.Parameter.KEYWORD_ONLY),
        inspect.Parameter("status", inspect.Parameter.KEYWORD_ONLY),
        inspect.Parameter(
            "result", inspect.Parameter.KEYWORD_ONLY, default=None
        ),
        inspect.Parameter(
            "error", inspect.Parameter.KEYWORD_ONLY, default=None
        ),
    )
)
_RECONCILE_SIGNATURE = inspect.Signature(
    parameters=(
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("run_id", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("owner", inspect.Parameter.KEYWORD_ONLY),
        inspect.Parameter(
            "lister", inspect.Parameter.KEYWORD_ONLY, default=None
        ),
        inspect.Parameter(
            "object_lister", inspect.Parameter.KEYWORD_ONLY, default=None
        ),
        inspect.Parameter(
            "manifest_loader", inspect.Parameter.KEYWORD_ONLY, default=None
        ),
    )
)


@dataclass(frozen=True, slots=True)
class _ReconcileRequest:
    """Validated arguments for one run reconciliation pass."""

    run_id: str
    owner: str
    lister: Any
    object_lister: Any
    manifest_loader: Any
