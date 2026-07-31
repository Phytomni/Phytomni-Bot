# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
"""Static callable contracts for conversation-context projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import (
    TYPE_CHECKING,
    Annotated,
    Literal,
    NotRequired,
    Protocol,
    TypedDict,
    Unpack,
)
from uuid import UUID

from ...config.defaults import ApiConfig
from ..locale import SupportedLocale
from .models import ArtifactRefV1, BusinessContext, ContextProjection

if TYPE_CHECKING:
    from .projection import TokenEstimator


class ProjectionKwargs(TypedDict):
    """Keyword arguments accepted by the projection callable."""

    conversation_key: Annotated[UUID, "projection-contract"]
    current_query: Annotated[str, "projection-contract"]
    locale: Annotated[SupportedLocale, "projection-contract"]
    selected_agent_id: Annotated[str, "projection-contract"]
    context: Annotated[BusinessContext, "projection-contract"]
    authorized_artifacts: Annotated[
        Sequence[ArtifactRefV1], "projection-contract"
    ]
    api_config: Annotated[ApiConfig, "projection-contract"]
    estimator: NotRequired[
        Annotated[TokenEstimator | None, "projection-contract"]
    ]
    exclude_current_user_turn: NotRequired[
        Annotated[bool, "projection-contract"]
    ]


class RebuildKwargs(TypedDict):
    """Keyword arguments accepted by the context rebuild callable."""

    conversation_key: Annotated[UUID, "rebuild-contract"]
    ledger_entries: Annotated[
        Sequence[Mapping[str, object]], "rebuild-contract"
    ]
    artifact_refs: Annotated[Sequence[ArtifactRefV1], "rebuild-contract"]
    ledger_cursor: Annotated[int, "rebuild-contract"]
    ledger_version: Annotated[str, "rebuild-contract"]
    observed_mode: Annotated[Literal["instant", "expert"], "rebuild-contract"]


class ProjectionBuilder(Protocol):
    """Statically typed projection callable contract."""

    def __call__(
        self, **kwargs: Unpack[ProjectionKwargs]
    ) -> ContextProjection: ...

    @property
    def __name__(self) -> str: ...

    __qualname__: str


class RebuildBuilder(Protocol):
    """Statically typed context rebuild callable contract."""

    def __call__(self, **kwargs: Unpack[RebuildKwargs]) -> BusinessContext: ...

    @property
    def __name__(self) -> str: ...

    __qualname__: str
