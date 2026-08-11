# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Compatibility facade for MCP result formatting.

Domain formatters live under :mod:`mcp.formatting`. This module keeps the
historic imports, including the two private citation helpers used by the
server test contract, object-identical during the migration.
"""

from typing import TypedDict

from . import universal_failures as _universal_failures
from .formatting import agui as _formatting_agui
from .formatting import cited as _formatting_cited
from .formatting import dispatch as _formatting_dispatch
from .formatting import execution as _formatting_execution
from .formatting import models as _formatting_models
from .formatting import redaction as _formatting_redaction
from .formatting import tasks as _formatting_tasks

project_degraded_metadata = _universal_failures.project_degraded_metadata
project_interop_metadata = _universal_failures.project_interop_metadata
project_universal_failure_metadata = (
    _universal_failures.project_universal_failure_metadata
)
redact_failure_message = _universal_failures.redact_failure_message

AguiEvent = _formatting_agui.AguiEvent
CONTEXT_STAGED_CUSTOM_NAME = "phyto.context_staged"
custom = _formatting_agui.custom


class ContextStagedPayload(TypedDict):
    """Validated fields carried by the conversation-context stage frame."""

    turn_id: str
    selected_agent_id: str
    route_source: str
    route_reason_code: str
    base_business_context_version: int
    proposed_business_context_version: int
    last_applied_ledger_cursor: int
    context_truncated: bool
    context_rebuilt: bool
    context_degraded: bool


def context_staged(payload: ContextStagedPayload) -> AguiEvent:
    """Return the bounded V1 conversation-context staging custom frame."""
    return custom(
        CONTEXT_STAGED_CUSTOM_NAME,
        {
            "schema_version": 1,
            "turn_id": payload["turn_id"],
            "selected_agent_id": payload["selected_agent_id"],
            "route_source": payload["route_source"],
            "route_reason_code": payload["route_reason_code"],
            "base_business_context_version": (
                payload["base_business_context_version"]
            ),
            "proposed_business_context_version": (
                payload["proposed_business_context_version"]
            ),
            "last_applied_ledger_cursor": (
                payload["last_applied_ledger_cursor"]
            ),
            "context_truncated": payload["context_truncated"],
            "context_rebuilt": payload["context_rebuilt"],
            "context_degraded": payload["context_degraded"],
        },
    )


run_error = _formatting_agui.run_error
run_finished = _formatting_agui.run_finished
run_started = _formatting_agui.run_started
step_started = _formatting_agui.step_started
text_message_content = _formatting_agui.text_message_content
text_message_end = _formatting_agui.text_message_end
text_message_start = _formatting_agui.text_message_start

FormattedToolChunk = _formatting_models.FormattedToolChunk
ExecutionProjection = _formatting_models.ExecutionProjection
ExecutionWarning = _formatting_models.ExecutionWarning
FormattedToolResult = _formatting_models.FormattedToolResult
ReportExecution = _formatting_models.ReportExecution
ToolResultEnvelope = _formatting_models.ToolResultEnvelope
format_tool_chunk = _formatting_models.format_tool_chunk

build_tool_result_envelope = _formatting_dispatch.build_tool_result_envelope
apply_compatibility_projection = (
    _formatting_execution.apply_compatibility_projection
)
build_execution_projection = _formatting_execution.build_execution_projection
format_tool_result = _formatting_dispatch.format_tool_result
is_cited_tool = _formatting_dispatch.is_cited_tool
_normalize_tool_name = _formatting_dispatch.normalize_tool_name

resolve_debug = _formatting_redaction.resolve_debug
_is_sensitive_key = _formatting_redaction.is_sensitive_key
_sanitize_raw = _formatting_redaction.sanitize_raw
strip_agent_result = _formatting_redaction.strip_agent_result
strip_chat_completion = _formatting_redaction.strip_chat_completion

_format_message_result = _formatting_cited.format_message_result
_format_cited_message_result = _formatting_cited.format_cited_message_result
_normalize_citations = _formatting_cited.normalize_citations
_reference_payload = _formatting_cited.reference_payload

_format_data_result = _formatting_tasks.format_data_result
_format_task_result = _formatting_tasks.format_task_result
_format_analyst_task_result = _formatting_tasks.format_analyst_task_result
_format_network_task_result = _formatting_tasks.format_network_task_result
_format_deep_genome_result = _formatting_tasks.format_deep_genome_result
_format_in_silico_result = _formatting_tasks.format_in_silico_result
_format_design_result = _formatting_tasks.format_design_result
_format_task_status_result = _formatting_tasks.format_task_status_result
