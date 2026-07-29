from collections.abc import Mapping, Sequence
from typing import Literal, Protocol
from uuid import UUID

from ...config.defaults import ApiConfig
from ..locale import SupportedLocale
from .models import (
    ArtifactRefV1,
    BusinessContext,
    ContextProjection,
)
from .projection import TokenEstimator

class ProjectionBuilder(Protocol):
    def __call__(
        self,
        *,
        conversation_key: UUID,
        current_query: str,
        locale: SupportedLocale,
        selected_agent_id: str,
        context: BusinessContext,
        authorized_artifacts: Sequence[ArtifactRefV1],
        api_config: ApiConfig,
        estimator: TokenEstimator | None = ...,
        exclude_current_user_turn: bool = ...,
    ) -> ContextProjection: ...


class RebuildBuilder(Protocol):
    def __call__(
        self,
        *,
        conversation_key: UUID,
        ledger_entries: Sequence[Mapping[str, object]],
        artifact_refs: Sequence[ArtifactRefV1],
        ledger_cursor: int,
        ledger_version: str,
        observed_mode: Literal["instant", "expert"],
    ) -> BusinessContext: ...
