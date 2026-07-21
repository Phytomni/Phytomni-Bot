# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Typed remote-operation seams for the DeepGenome workflow.

The dispatch mixin owns graph routing and state projection.  This module owns
the side-effecting calls to the analysis platform and Object Storage Service,
including their relay/direct selection and bounded request configuration.
Keeping these operations behind one injectable adapter makes protocol-boundary
tests independent of live credentials while preserving the existing graph
behavior.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict, Unpack

from ...config.relay_mode import relay_mode_enabled
from ...graphs.analyst_dispatch_adapters import submit_analyst_via_subgraph
from ...storage.obs_storage import normalize_obs_object_key, obsfs_path_for
from ...storage.path_policy import RunIdentity
from ...storage.scratch import ScratchTarget, resolve_scratch_dir
from ..analyst.storage import download_obs_out, download_obs_out_via_relay
from ..analyst.task_ops import task_delete, task_status
from ..design.agent import promoter_design_for_gene, protein_structure_for_gene
from .coordinator import (
    RemoteSubmission,
    WorkItemOutcome,
    normalize_submission,
    poll_work_item,
)
from .summary import build_design_work_item_summary

logger = logging.getLogger(__name__)

_BEST_EFFORT_ERRORS: tuple[type[Exception], ...] = (Exception,)

ANALYSIS_TARGET_FILE_FEATURE_MAP = {
    "evolution_analysis": [".md", ".png", ".summary", ".legend"],
    "haplotypes_analysis": [".png", ".summary", ".legend"],
    "fst_analysis": [".png"],
    "enrichment_analysis": [".png", ".summary", ".legend"],
    "protein_structure_analysis": ["sample_0.cif", ".summary", ".legend"],
    "promoter_analysis": ["motif_all_logo.png", ".summary", ".legend"],
    "gene_expression_tissues": [".png", ".summary", ".legend"],
    "gene_expression_cultivars": [".png", ".summary", ".legend"],
    "gene_expression_genotypes": [".png", ".summary", ".legend"],
    "gene_expression_treatments": [".png", ".summary", ".legend"],
    "single_cell_analysis": [".png", ".summary", ".legend"],
    "smep_analysis": [".png", ".summary", ".legend"],
    "smoc_analysis": [".png", ".summary", ".legend"],
}
DEFAULT_TARGET_FILE_FEATURE = [".png", ".summary", ".legend"]

StatusCall = Callable[..., Awaitable[Any]]
DeleteCall = Callable[..., Awaitable[Any]]
SubmitCall = Callable[..., Awaitable[Any]]
DownloadCall = Callable[..., Any]
RelayDownloadCall = Callable[..., Awaitable[list[str]]]
PromptParts = Callable[[Any], tuple[str, dict[str, Any], str, str]]
PollCall = Callable[..., Awaitable[WorkItemOutcome]]
SummaryBuilder = Callable[[str], str]


class RemoteIOHooks(TypedDict, total=False):
    """Optional side-effect overrides used by protocol-boundary tests."""

    task_status: StatusCall
    task_delete: DeleteCall
    analyst_submit: SubmitCall
    protein_structure: SubmitCall
    promoter_design: SubmitCall
    obsfs_path: Callable[..., Path]
    relay_enabled: Callable[[], bool]
    relay_download: RelayDownloadCall
    sdk_download: DownloadCall
    scratch_dir: Callable[..., str]
    poll_work_item: PollCall
    download_result: Callable[[Any, str, RunIdentity], Awaitable[str]]


class RemotePollOptions(TypedDict, total=False):
    """Optional state and summary bindings for one polling call."""

    summary_builder: SummaryBuilder | None
    tracking: Any | None
    work_item_key: str | None
    transition_sink_factory: Callable[[], Any] | None


def _empty_hooks() -> RemoteIOHooks:
    """Return an empty typed hook mapping for the production adapter."""
    return {}


def _read_result_markdown(results_dir: str) -> str:
    """Read deterministic nonblank Markdown summaries from a result dir."""
    root = Path(results_dir)
    summaries: list[str] = []
    for summary_path in sorted(root.rglob("*.summary")):
        if not summary_path.is_file():
            continue
        content = summary_path.read_text(encoding="utf-8").strip()
        if content:
            summaries.append(content)
    return "\n\n".join(summaries)


@dataclass(slots=True)
class _PollTarget:
    """Immutable identity and request context for one accepted submission."""

    submission: RemoteSubmission
    context: Any
    run_identity: RunIdentity


@dataclass(slots=True)
class _PollBinding:
    """Bind one accepted submission to the coordinator callback protocol."""

    remote_io: Any
    target: _PollTarget
    sink: Any
    tracked_work_item: str
    summary_builder: SummaryBuilder | None
    resolved_results_dir: str | None = None

    async def read_remote_status(
        self,
        poll_task_id: str,
        request_timeout: float,
    ) -> Any:
        """Read one remote status through the injected task seam."""
        status = self.remote_io.hook("task_status", task_status)
        return await status(
            poll_task_id,
            timeout=request_timeout,
            **self.remote_io.analysis_request_kwargs(),
        )

    async def resolve_remote_result(
        self,
        accepted: RemoteSubmission,
    ) -> str:
        """Download one result and build a nonblank summary."""
        download_override = self.remote_io.hooks.get("download_result")
        if download_override is not None:
            self.resolved_results_dir = await download_override(
                self.target.context,
                accepted.output_dir,
                self.target.run_identity,
            )
        else:
            self.resolved_results_dir = (
                await self.remote_io.download_analysis_result(
                    self.target.context,
                    accepted.output_dir,
                    self.target.run_identity,
                )
            )
        if self.resolved_results_dir is None:
            raise RuntimeError("analysis result directory unavailable")
        resolver = self.summary_builder or self._promoter_resolver()
        return (resolver or _read_result_markdown)(self.resolved_results_dir)

    def _promoter_resolver(self) -> SummaryBuilder | None:
        """Return the artifact fallback for promoter analysis."""
        if self.target.context.analysis_type != "promoter_analysis":
            return None

        def resolve_promoter(path: str) -> str:
            """Build the promoter artifact fallback summary."""
            return build_design_work_item_summary("promoter_design", path)

        return resolve_promoter

    async def record_transition(
        self,
        status_name: str,
        summary: str | None,
        failure_reason: str | None,
    ) -> WorkItemOutcome:
        """Persist one local transition through the supplied sink."""
        return await self.sink.persist_work_item_transition(
            self.tracked_work_item,
            self.target.submission,
            status_name,
            summary,
            failure_reason,
        )


@dataclass(slots=True)
class DeepGenomeRemoteIO:
    """Own DeepGenome's remote task and result-storage operations.

    ``hooks`` carries optional dependency-injection seams.  A missing hook
    means that the production implementation is looked up when the method is
    called, so tests can still monkeypatch the module-level boundary without
    rebuilding the adapter.
    """

    config: Any
    sensitive_config: Any
    analyst_agent: Any | None = None
    hooks: RemoteIOHooks = field(default_factory=_empty_hooks)

    def hook(self, name: str, default: Any) -> Any:
        """Return an injected hook or the production implementation."""
        return self.hooks.get(name, default)

    def analysis_request_kwargs(self) -> dict[str, Any]:
        """Build bounded platform kwargs for status and cancellation calls."""
        values: dict[str, Any] = {}
        for config_name, argument_name in (
            ("ANALYSIS_URL", "analysis_url"),
            ("ANALYSIS_REGION", "region"),
            ("RETRIABLE_CODES", "retriable_codes"),
            ("MAX_RETRIES", "max_retries"),
        ):
            value = getattr(self.config, config_name, None)
            if value is not None:
                values[argument_name] = value
        return values

    async def cancel_submission(self, submission: RemoteSubmission) -> None:
        """Best-effort terminate only the caller-owned remote task id."""
        delete = self.hook("task_delete", task_delete)
        try:
            await delete(
                submission.submitted_task_id,
                timeout=getattr(self.config, "TIMEOUT", 600.0),
                **self.analysis_request_kwargs(),
            )
        except _BEST_EFFORT_ERRORS as exc:
            logger.warning(
                "DeepGenome cancellation unavailable; error_type=%s",
                type(exc).__name__,
            )

    async def submit_analysis_task(
        self,
        context: Any,
        *,
        prompt_parts: PromptParts | None,
    ) -> dict[str, Any] | RemoteSubmission:
        """Submit one analysis using the selected producer or subgraph."""
        producer = self._producer_for(context.analysis_type)
        if producer is not None:
            response = await producer(
                species_code=context.species_code,
                gene_id=context.gene_id,
                output_dir=context.output_dir,
                is_polling=False,
            )
            return self._normalize_submission(response)
        if prompt_parts is None:
            raise ValueError("analysis prompt builder unavailable")
        goal, data_list, meta, compute_resource = prompt_parts(context)
        request = {
            "analysis_type": context.analysis_type,
            "target_id": context.gene_id,
            "output_dir": context.output_dir,
            "prompt_parts": (goal, meta, data_list),
            "compute_resource": compute_resource,
        }
        submit = self.hook("analyst_submit", submit_analyst_via_subgraph)
        response = await submit(
            self.analyst_agent,
            self.config,
            self.sensitive_config,
            request,
            is_polling=False,
        )
        return self._normalize_submission(response)

    def _producer_for(self, analysis_type: str) -> SubmitCall | None:
        """Return the design producer for one special analysis type."""
        if analysis_type == "protein_structure_analysis":
            return self.hook("protein_structure", protein_structure_for_gene)
        if analysis_type == "promoter_analysis":
            return self.hook("promoter_design", promoter_design_for_gene)
        return None

    @staticmethod
    def _normalize_submission(
        response: Any,
    ) -> dict[str, Any] | RemoteSubmission:
        """Normalize a mapping while retaining legacy failure payloads."""
        if not isinstance(response, dict):
            raise ValueError("invalid analysis submission")
        if response.get("task_status") == "FAILED_AT_AGENT_LEVEL":
            return response
        return normalize_submission(response)

    async def poll_remote_submission(
        self,
        submission: RemoteSubmission,
        context: Any,
        run_identity: RunIdentity,
        **options: Unpack[RemotePollOptions],
    ) -> tuple[WorkItemOutcome, str | None]:
        """Poll one submission and resolve its local Markdown result."""
        sink = self._resolve_sink(options)
        binding = _PollBinding(
            remote_io=self,
            target=_PollTarget(submission, context, run_identity),
            sink=sink,
            tracked_work_item=options.get("work_item_key")
            or context.analysis_type,
            summary_builder=options.get("summary_builder"),
        )
        poller = self.hook("poll_work_item", poll_work_item)
        outcome = await poller(
            submission,
            status_reader=binding.read_remote_status,
            result_resolver=binding.resolve_remote_result,
            transition_sink=binding.record_transition,
            request_timeout=float(getattr(self.config, "TIMEOUT", 600.0)),
            poll_interval=float(getattr(self.config, "POLL_INTERVAL", 300.0)),
            deadline_seconds=float(getattr(self.config, "MAX_POLL", 86400.0)),
        )
        return outcome, binding.resolved_results_dir

    @staticmethod
    def _resolve_sink(options: RemotePollOptions) -> Any:
        """Resolve an injected tracking sink or its factory."""
        tracking = options.get("tracking")
        if tracking is not None:
            return tracking
        factory = options.get("transition_sink_factory")
        if factory is None:
            raise ValueError("DeepGenome transition sink unavailable")
        return factory()

    async def download_analysis_result(
        self,
        context: Any,
        output_path: str,
        run_identity: RunIdentity,
    ) -> str:
        """Return a readable result directory, downloading only when needed."""
        existing_dir = self.obsfs_analysis_result_dir(output_path)
        if existing_dir is not None:
            return existing_dir
        obs_output_path, scratch_root, local_dir, features = (
            self._download_context(context, output_path, run_identity)
        )
        if self.hook("relay_enabled", relay_mode_enabled)():
            await self._download_via_relay(
                context, obs_output_path, scratch_root, features
            )
            return str(local_dir)
        await self._download_via_sdk(
            context, obs_output_path, scratch_root, features
        )
        return str(local_dir)

    def _download_context(
        self,
        context: Any,
        output_path: str,
        run_identity: RunIdentity,
    ) -> tuple[str, str, Path, list[str]]:
        """Resolve the safe object key and run-scoped local download path."""
        obs_output_path = normalize_obs_object_key(
            output_path, self.config.BUCKET_NAME
        )
        scratch = self.hook("scratch_dir", resolve_scratch_dir)
        scratch_root = scratch(
            "downloads",
            run_identity,
            context.analysis_type,
            ScratchTarget(
                bucket_name=self.config.BUCKET_NAME,
                local_fallback=Path(self.config.DEEPGENOME_OUT),
            ),
        )
        local_results_dir = Path(scratch_root) / context.gene_id
        target_file_feature = _target_file_features(context.analysis_type)
        return (
            obs_output_path,
            scratch_root,
            local_results_dir,
            target_file_feature,
        )

    async def _download_via_relay(
        self,
        context: Any,
        obs_output_path: str,
        scratch_root: str,
        features: list[str],
    ) -> None:
        """Download selected result objects through the operator relay."""
        relay_download = self.hook(
            "relay_download", download_obs_out_via_relay
        )
        statuses = await relay_download(
            task_dir=context.gene_id,
            obs_output_path=obs_output_path,
            download_path=scratch_root,
            target_file_feature=features,
            if_download_all=False,
        )
        if not statuses:
            raise RuntimeError(
                "relay returned no analysis results for " f"{obs_output_path}"
            )

    async def _download_via_sdk(
        self,
        context: Any,
        obs_output_path: str,
        scratch_root: str,
        features: list[str],
    ) -> None:
        """Download selected result objects with direct OBS credentials."""
        try:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            sdk_download = self.hook("sdk_download", download_obs_out)
            deque(
                sdk_download(
                    task_dir=context.gene_id,
                    obs_output_path=obs_output_path,
                    download_path=scratch_root,
                    access_key_id=access_key_id,
                    secret_access_key=secret_access_key,
                    obs_server=self.config.OBS_SERVER,
                    bucket_name=self.config.BUCKET_NAME,
                    target_file_feature=features,
                    if_download_all=False,
                ),
                maxlen=0,
            )
        except OSError as exc:
            logger.warning(
                "Failed to download results (continuing); error_type=%s",
                type(exc).__name__,
            )

    def obsfs_analysis_result_dir(self, output_path: str) -> str | None:
        """Return the obsfs result directory when it is directly readable."""
        obsfs_path = self.hook("obsfs_path", obsfs_path_for)
        try:
            candidate = obsfs_path(output_path, self.config.BUCKET_NAME)
            if candidate.is_dir():
                return str(candidate)
        except OSError:
            return None
        local_path = Path(output_path)
        if local_path.is_dir():
            return str(local_path)
        return None


def _target_file_features(analysis_type: str) -> list[str]:
    """Return the stable output filter for one analysis type."""
    return list(
        ANALYSIS_TARGET_FILE_FEATURE_MAP.get(
            analysis_type, DEFAULT_TARGET_FILE_FEATURE
        )
    )


__all__ = [
    "ANALYSIS_TARGET_FILE_FEATURE_MAP",
    "DEFAULT_TARGET_FILE_FEATURE",
    "DeepGenomeRemoteIO",
    "RemoteIOHooks",
]
