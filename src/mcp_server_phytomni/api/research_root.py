# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Default HTTP Research root composition.

The HTTP adapter owns only opaque asset IDs. This module turns the admitted
owner-bound snapshots into the existing Research coordinator ports and leaves
dataset descriptions empty when the caller asks the remote platform to inspect
the uploaded files itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from os import stat
from typing import TYPE_CHECKING, Any

from ..agents.chat.service import _cached_chat_app
from ..agents.research.document_evidence import (
    ConvertedResearchSection,
    ManagedDocumentObservation,
    ManagedDocumentPayload,
    ResearchEvidenceRequest,
    extract_research_evidence,
)
from ..agents.research.goal_extraction import (
    EvidenceGoalProvider,
    ResearchGoalExtractionDependencies,
)
from ..agents.research.input_contracts import (
    ResearchCoordinatorDependencies,
    ResearchCoordinatorRequest,
)
from ..agents.research.input_inventory import (
    ManagedResearchAssetResolver,
    ResearchInputInventory,
    ResearchInventoryRequest,
    build_research_inventory,
    revalidate_research_inventory,
)
from ..agents.research.input_preparation import (
    prepare_research_input_for_remote_inspection,
)
from ..agents.research.planning import (
    ResearchPlan,
    ResearchPlanningRequest,
    build_research_plan,
)
from ..common.prompts import get_prompt
from ..config.api_limits import ApiLimitsConfig
from ..config.defaults import (
    InSilicoResearchConfig,
    ServerConfig,
    resolve_compute_resource,
)
from ..config.settings import get_sensitive_config
from ..storage.downloads import convert_document_file, download_obs_source
from ..storage.research_objects import ResearchObjectMetadataPort
from .asset_resolver import bind_research_asset_resolver

if TYPE_CHECKING:
    from .research_input import ResearchAdmissionRequest

__all__ = ["build_default_research_root_request_factory"]


@dataclass(frozen=True, slots=True)
class _ManagedDocumentDownloader:
    """Download one authorized document through the active storage path."""

    source: ServerConfig

    @property
    def contract_name(self) -> str:
        """Identify the managed document downloader contract."""
        return "managed_document_downloader"

    def observe(self, entry: Any) -> ManagedDocumentObservation:
        """Use the inventory snapshot fenced by the admission sequence."""
        return ManagedDocumentObservation(
            exact_reference=entry.exact_reference,
            snapshot=entry.snapshot,
        )

    async def download(self, entry: Any) -> ManagedDocumentPayload:
        """Stage one OBS object to a local file without buffering the body."""
        resolved = await download_obs_source(
            entry.exact_reference,
            self.source.TEMP_DIR,
            bucket_name=self.source.BUCKET_NAME,
        )
        return ManagedDocumentPayload(
            path=resolved.file_path,
            size_bytes=stat(resolved.file_path).st_size,
            cleanup=resolved.cleanup,
        )


class _MarkItDownDocumentConverter:
    """Convert one staged document through the shared MarkItDown helper."""

    @property
    def contract_name(self) -> str:
        """Identify the managed document converter contract."""
        return "managed_document_converter"

    def convert(
        self, entry: Any, payload: ManagedDocumentPayload
    ) -> tuple[ConvertedResearchSection, ...]:
        """Return deterministic page/section units from the staged file."""
        converted = convert_document_file(payload.path, cleanup=False)
        is_pdf = entry.safe_basename.casefold().endswith(".pdf")
        return tuple(
            ConvertedResearchSection(
                ordinal=ordinal,
                label=(f"page-{ordinal}" if is_pdf else f"section-{ordinal}"),
                text=text,
            )
            for ordinal, text in enumerate(converted.sections, start=1)
        )


def build_default_research_root_request_factory(
    *,
    metadata_port: ResearchObjectMetadataPort,
    asset_resolver_factory: Callable[[], Any] | None,
) -> Callable[[ResearchAdmissionRequest], ResearchCoordinatorRequest]:
    """Build the default owner-bound Research coordinator request factory."""
    if not all(
        callable(getattr(metadata_port, method))
        for method in ("resolve", "verify", "revoke")
    ):
        raise TypeError(
            "metadata_port must implement Research object metadata"
        )

    def build(
        admission: ResearchAdmissionRequest,
    ) -> ResearchCoordinatorRequest:
        """Bind one admission to fresh transient evidence and ports."""
        limits = ApiLimitsConfig()
        source = ServerConfig()
        inventory_request = ResearchInventoryRequest(
            parsed_input=admission.parsed_input,
            managed_assets=admission.managed_snapshot,
            configured_bucket=source.BUCKET_NAME,
            max_managed_references=limits.API_MAX_ATTACHMENTS_PER_REQUEST,
            max_pasted_references=limits.API_MAX_RESEARCH_DATASET_PATHS,
            max_combined_references=limits.API_MAX_RESEARCH_INPUT_REFERENCES,
        )
        managed_resolver: ManagedResearchAssetResolver | None = None
        if admission.managed_snapshot:
            if not callable(asset_resolver_factory):
                raise RuntimeError(
                    "Research managed asset resolver is unavailable"
                )
            managed_resolver = bind_research_asset_resolver(
                owner=admission.owner,
                resolver=asset_resolver_factory(),
            )
        downloader = _ManagedDocumentDownloader(source)
        converter = _MarkItDownDocumentConverter()

        async def build_inventory(
            request: ResearchCoordinatorRequest,
        ) -> ResearchInputInventory:
            return await build_research_inventory(
                request.inventory_request,
                metadata_port,
            )

        async def extract_evidence(
            request: ResearchCoordinatorRequest,
        ) -> Any:
            inventory = request.inventory_request
            return await extract_research_evidence(
                ResearchEvidenceRequest(
                    inventory=inventory,
                    effective_query=admission.parsed_input.effective_query,
                    effective_to_original=(
                        admission.parsed_input.effective_to_original
                    ),
                ),
                downloader,
                converter,
            )

        async def resolve_descriptions(
            request: ResearchCoordinatorRequest,
        ) -> None:
            """Keep empty descriptions so the remote model inspects files."""
            del request
            return None

        def join_prepared(
            inventory: ResearchInputInventory,
            resolution: Any,
        ) -> Any:
            del resolution
            return prepare_research_input_for_remote_inspection(
                inventory,
                effective_query=admission.parsed_input.effective_query,
            )

        async def revalidate(
            request: ResearchCoordinatorRequest,
        ) -> ResearchInputInventory:
            return await revalidate_research_inventory(
                inventory_request,
                request.inventory_request,
                metadata_port,
                managed_asset_resolver=managed_resolver,
            )

        async def plan_builder(
            prepared: Any,
            request: ResearchCoordinatorRequest,
        ) -> ResearchPlan:
            provider = EvidenceGoalProvider(
                ResearchGoalExtractionDependencies(
                    in_silico_config=InSilicoResearchConfig(),
                    sensitive_config=get_sensitive_config(),
                    prompt_builder=get_prompt,
                    chat_app_factory=_cached_chat_app,
                )
            )
            return await build_research_plan(
                ResearchPlanningRequest(
                    run_id=request.run_id,
                    prepared=prepared,
                    evidence=request.evidence,
                    locale=admission.locale,
                    compute_resource=resolve_compute_resource(
                        InSilicoResearchConfig
                    ),
                    interop_mode=admission.interop_mode,
                    interop_targets=admission.interop_targets,
                ),
                provider,
            )

        dependencies = ResearchCoordinatorDependencies(
            build_inventory=build_inventory,
            extract_evidence=extract_evidence,
            resolve_descriptions=resolve_descriptions,
            revalidate_inventory=revalidate,
            join_prepared=join_prepared,
            plan_builder=plan_builder,
        )
        return ResearchCoordinatorRequest(
            run_id="research-http-root",
            inventory_request=inventory_request,
            dependencies=dependencies,
            effective_query=admission.parsed_input.effective_query,
        )

    return build


def bind_default_research_root_request_factory(
    coordinator: Any,
    runtime: Any,
    *,
    asset_resolver_factory: Callable[[], Any] | None,
) -> Any:
    """Attach the default root factory to the registered runtime state."""
    if runtime is None:
        raise RuntimeError("Research production runtime was not registered")
    metadata_port = getattr(coordinator, "metadata_port", None)
    if metadata_port is None:
        raise RuntimeError("Research production runtime has no metadata port")
    factory = build_default_research_root_request_factory(
        metadata_port=metadata_port,
        asset_resolver_factory=asset_resolver_factory,
    )
    return replace(runtime, root_request_factory=factory)
