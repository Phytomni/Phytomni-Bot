# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Safe failure projection for durable Research root launches."""

from __future__ import annotations

import inspect
from typing import Any

from ..agents.research.input_contracts import (
    ResearchInputFailure,
    research_input_failure,
)
from ..runtime.research_input_store_support import AdmissionLaunchFailure


def launch_failure(
    store: Any,
    run_id: str,
    failure: ResearchInputFailure | None = None,
) -> ResearchInputFailure:
    """Persist one classified launch failure before exposing it."""
    failure = failure or research_input_failure(
        "research_input_resolution_unavailable",
        "Research execution is unavailable.",
        http_status_hint=503,
        retryable=True,
        stage="input_resolution",
    )
    marker = getattr(store, "mark_admission_launch_failed", None)
    if callable(marker):
        marker(
            run_id,
            AdmissionLaunchFailure(
                code=failure.code,
                retryable=failure.retryable,
                status_hint=failure.http_status_hint,
                stage=failure.stage,
            ),
        )
    return failure


async def launch_worker(
    launcher: Any,
    request: Any,
    admitted: Any,
    admission: Any,
    store: Any,
) -> None:
    """Launch a root only for its durable admission owner."""
    if not admitted.worker_owner or launcher is None:
        return
    try:
        parameters = inspect.signature(launcher).parameters.values()
        positional = tuple(
            parameter
            for parameter in parameters
            if parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        )
        accepts_admission = len(positional) >= 3 or any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_admission = True
    try:
        launched = (
            launcher(request, admitted, admission)
            if accepts_admission
            else launcher(request, admitted)
        )
        if inspect.isawaitable(launched):
            launched = await launched
    except ResearchInputFailure as exc:
        raise launch_failure(store, admitted.run_id, exc) from exc
    except Exception as exc:
        raise launch_failure(store, admitted.run_id) from exc
    if launched is False:
        raise launch_failure(store, admitted.run_id)
