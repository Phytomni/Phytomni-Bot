# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Immutable public capability descriptors for native API agents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from ..runtime.attachment_assets import ResolvedAttachmentBundle
from ..runtime.resumable_uploads import (
    CAPABILITY_TTL,
    MAX_ACTIVE_ASSETS,
    MAX_UPLOAD_BYTES,
    PART_SIZE_BYTES,
    SESSION_TTL,
    UPLOAD_PROTOCOL,
)

__all__ = [
    "AGENT_CAPABILITIES",
    "AttachmentCapability",
    "AgentCapability",
    "DatasetCapability",
    "DocumentContextCapability",
    "agent_supports_attachment_channels",
    "filter_tools_for_attachment_channels",
    "get_agent_slug_for_tool",
    "get_attachment_capability",
    "get_agent_capability",
    "required_attachment_channels",
    "serialize_file_upload_capability",
    "serialize_agent_capability",
]


MAX_FILE_BYTES = 26_214_400
MAX_FILES = 10
MAX_TOTAL_BYTES = 52_428_800
DOCUMENT_EXTENSIONS = ("pdf", "docx", "pptx", "xls", "xlsx", "msg")
MAX_PARALLEL_PARTS = 4

_UPLOAD_ROUTES: tuple[dict[str, str], ...] = (
    {
        "method": "POST",
        "path": "/v1/files",
        "plane": "control",
        "auth": "service_scope",
    },
    {
        "method": "POST",
        "path": "/v1/files/{asset_id}/capability",
        "plane": "control",
        "auth": "service_scope",
    },
    {
        "method": "HEAD",
        "path": "/v1/files/{asset_id}",
        "plane": "data",
        "auth": "asset_capability",
    },
    {
        "method": "PUT",
        "path": "/v1/files/{asset_id}/parts/{part_number}",
        "plane": "data",
        "auth": "asset_capability",
    },
    {
        "method": "POST",
        "path": "/v1/files/{asset_id}/complete",
        "plane": "data",
        "auth": "asset_capability",
    },
    {
        "method": "DELETE",
        "path": "/v1/files/{asset_id}",
        "plane": "data",
        "auth": "asset_capability",
    },
)


@dataclass(frozen=True)
class DocumentContextCapability:
    """Limits and input shape for document-context attachments."""

    argument: str = "obs_file_list"
    extensions: tuple[str, ...] = DOCUMENT_EXTENSIONS
    max_file_bytes: int = MAX_FILE_BYTES
    max_files: int = MAX_FILES
    max_total_bytes: int = MAX_TOTAL_BYTES

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize the immutable descriptor into JSON-compatible values."""
        return {
            "argument": self.argument,
            "extensions": list(self.extensions),
            "max_file_bytes": self.max_file_bytes,
            "max_files": self.max_files,
            "max_total_bytes": self.max_total_bytes,
        }


@dataclass(frozen=True)
class _AttachmentLimits:
    """Shared size limits for one attachment channel."""

    max_file_bytes: int = MAX_FILE_BYTES
    max_files: int = MAX_FILES
    max_total_bytes: int = MAX_TOTAL_BYTES


@dataclass(frozen=True)
class DatasetCapability:
    """Limits and input shape for structured CSV dataset attachments."""

    argument: str = "data_list"
    formats: tuple[str, ...] = ("csv",)
    encoding: tuple[str, ...] = ("utf-8", "utf-8-bom")
    delimiter: str = ","
    requires_description: bool = True
    compressed: bool = False
    _limits: _AttachmentLimits = _AttachmentLimits()

    @property
    def max_file_bytes(self) -> int:
        """Return the per-file size limit."""
        return self._limits.max_file_bytes

    @property
    def max_files(self) -> int:
        """Return the per-request file-count limit."""
        return self._limits.max_files

    @property
    def max_total_bytes(self) -> int:
        """Return the per-request aggregate size limit."""
        return self._limits.max_total_bytes

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize the immutable descriptor into JSON-compatible values."""
        return {
            "argument": self.argument,
            "formats": list(self.formats),
            "encoding": list(self.encoding),
            "delimiter": self.delimiter,
            "requires_description": self.requires_description,
            "compressed": self.compressed,
            "max_file_bytes": self.max_file_bytes,
            "max_files": self.max_files,
            "max_total_bytes": self.max_total_bytes,
        }


@dataclass(frozen=True)
class AttachmentCapability:
    """Supported attachment channels for one agent."""

    document_context: DocumentContextCapability | None = None
    datasets: DatasetCapability | None = None
    expert_forwarding: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize unsupported channels as null, not false objects."""
        return {
            "document_context": (
                None
                if self.document_context is None
                else self.document_context.to_public_dict()
            ),
            "datasets": (
                None
                if self.datasets is None
                else self.datasets.to_public_dict()
            ),
            "expert_forwarding": self.expert_forwarding,
        }


@dataclass(frozen=True)
class AgentCapability:
    """Consumer-facing facts for one canonical agent slug."""

    streaming: bool = False
    interactive: bool = False
    report_states: tuple[str, ...] = ()
    artifacts: bool = False
    degraded_outcomes: bool = False
    attachments: AttachmentCapability = AttachmentCapability()

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize with JSON-compatible deterministic values."""
        return {
            "streaming": self.streaming,
            "interactive": self.interactive,
            "report_states": list(self.report_states),
            "artifacts": self.artifacts,
            "degraded_outcomes": self.degraded_outcomes,
            "attachments": self.attachments.to_public_dict(),
        }


_DOCUMENTS = DocumentContextCapability()
_DATASETS = DatasetCapability()

_CAPABILITIES: dict[str, AgentCapability] = {
    "chat": AgentCapability(
        streaming=True,
        interactive=True,
        attachments=AttachmentCapability(_DOCUMENTS, None, True),
    ),
    "knowledge": AgentCapability(
        streaming=True,
        attachments=AttachmentCapability(_DOCUMENTS, None, True),
    ),
    "data": AgentCapability(),
    "review": AgentCapability(
        streaming=True,
        interactive=True,
        attachments=AttachmentCapability(_DOCUMENTS, None, True),
    ),
    "brief_gene": AgentCapability(streaming=True),
    "analyst": AgentCapability(
        report_states=("final",),
        artifacts=True,
        degraded_outcomes=True,
        attachments=AttachmentCapability(_DOCUMENTS, _DATASETS, False),
    ),
    "deep_genome": AgentCapability(
        report_states=("intermediate", "final"),
        artifacts=True,
        degraded_outcomes=True,
    ),
    "research": AgentCapability(
        report_states=("final",),
        artifacts=True,
        degraded_outcomes=True,
        attachments=AttachmentCapability(_DOCUMENTS, _DATASETS, False),
    ),
    "design": AgentCapability(
        report_states=("final",),
        artifacts=True,
        degraded_outcomes=True,
        attachments=AttachmentCapability(_DOCUMENTS, None, False),
    ),
    "network": AgentCapability(
        report_states=("final",),
        artifacts=True,
        degraded_outcomes=True,
        attachments=AttachmentCapability(_DOCUMENTS, None, False),
    ),
}

_TOOL_TO_AGENT_SLUG: Mapping[str, str] = MappingProxyType(
    {
        "ChatAgent": "chat",
        "KnowledgeAgent": "knowledge",
        "DataAgent": "data",
        "ReviewAgent": "review",
        "BriefGeneAgent": "brief_gene",
        "AnalystAgent": "analyst",
        "DeepGenomeAgent": "deep_genome",
        "InSilicoResearchAgent": "research",
        "DigitalDesignAgent": "design",
        "GeneNetworkAgent": "network",
    }
)

AGENT_CAPABILITIES: Final[Mapping[str, AgentCapability]] = MappingProxyType(
    _CAPABILITIES
)


def get_agent_capability(slug: str) -> AgentCapability:
    """Return the explicit descriptor for ``slug`` or fail closed."""
    try:
        return AGENT_CAPABILITIES[slug]
    except KeyError as exc:
        raise KeyError(f"unknown agent capability slug: {slug}") from exc


def get_attachment_capability(slug: str) -> AttachmentCapability:
    """Return the attachment policy for ``slug`` or fail closed."""
    return get_agent_capability(slug).attachments


def get_agent_slug_for_tool(tool_name: str) -> str | None:
    """Return a canonical agent slug for a public MCP tool name."""
    return _TOOL_TO_AGENT_SLUG.get(tool_name)


def required_attachment_channels(
    bundle: ResolvedAttachmentBundle,
) -> frozenset[str]:
    """Return the exact attachment channels required by a resolved bundle."""
    channels: set[str] = set()
    if bundle.documents:
        channels.add("documents")
    if bundle.datasets:
        channels.add("datasets")
    return frozenset(channels)


def agent_supports_attachment_channels(
    agent: str,
    channels: frozenset[str],
) -> bool:
    """Return whether one canonical tool or slug supports every channel."""
    slug = get_agent_slug_for_tool(agent) or agent
    try:
        attachments = get_attachment_capability(slug)
    except KeyError:
        return False
    return all(
        (channel == "documents" and attachments.document_context is not None)
        or (channel == "datasets" and attachments.datasets is not None)
        for channel in channels
    )


def filter_tools_for_attachment_channels(
    *,
    allowed_tools: Sequence[str],
    channels: frozenset[str],
) -> tuple[str, ...]:
    """Retain known allowed tools that support all required channels."""
    return tuple(
        tool
        for tool in allowed_tools
        if get_agent_slug_for_tool(tool) is not None
        and agent_supports_attachment_channels(tool, channels)
    )


def serialize_agent_capability(slug: str) -> dict[str, Any]:
    """Return one JSON-compatible capability descriptor for ``slug``."""
    return get_agent_capability(slug).to_public_dict()


def serialize_file_upload_capability(
    limits: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Return a fresh, sanitized descriptor for the upload protocol."""
    resolved = {
        "max_file_bytes": MAX_UPLOAD_BYTES,
        "part_size_bytes": PART_SIZE_BYTES,
        "max_parallel_parts": MAX_PARALLEL_PARTS,
        "max_active_assets": MAX_ACTIVE_ASSETS,
        "capability_ttl_seconds": int(CAPABILITY_TTL.total_seconds()),
        "session_ttl_seconds": int(SESSION_TTL.total_seconds()),
    }
    if limits is not None:
        for key in resolved:
            if key in limits:
                resolved[key] = limits[key]
    return {
        "protocol": UPLOAD_PROTOCOL,
        "route_family": "resumable_files",
        "routes": [dict(route) for route in _UPLOAD_ROUTES],
        "limits": resolved,
    }
