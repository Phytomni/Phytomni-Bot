# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Small callable and fact contracts shared by Runtime instrumentation."""

from __future__ import annotations

from inspect import Parameter, Signature
from typing import TypedDict

ParameterField = tuple[str, object]
OptionalParameterField = tuple[str, object, object]


class ObservationFactIdentity(TypedDict):
    """Common causal identity carried by work-unit observation facts."""

    event_type: str
    status: str
    span_id: str
    parent_span_id: str | None
    work_unit_id: str
    attempt: int


def keyword_signature(
    required: tuple[ParameterField, ...],
    optional: tuple[OptionalParameterField, ...] = (),
    *,
    positional: tuple[ParameterField, ...] = (),
    return_annotation: object = Signature.empty,
) -> Signature:
    """Build a stable public signature around a typed ``**kwargs`` facade."""
    parameters = tuple(
        Parameter(name, Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation)
        for name, annotation in positional
    )
    parameters += tuple(
        Parameter(name, Parameter.KEYWORD_ONLY, annotation=annotation)
        for name, annotation in required
    )
    parameters += tuple(
        Parameter(
            name,
            Parameter.KEYWORD_ONLY,
            annotation=annotation,
            default=default,
        )
        for name, annotation, default in optional
    )
    return Signature(
        parameters=parameters,
        return_annotation=return_annotation,
    )
