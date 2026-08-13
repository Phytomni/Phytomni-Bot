# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Single typed inventory for relay-child and operator pool contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mcp_server_phytomni.runtime.outbound import OutboundPoolName

type RelayJsonMethod = Literal["GET", "POST", "PUT"]
type ServerTerminatedOwner = Literal["local", "control", "bi", "obs"]


@dataclass(frozen=True, slots=True)
class RelayGenericCall:
    """One production generic RelayClient call site."""

    source: str
    owner: str
    method: Literal["get_json", "post_json"]
    path: str
    pool: OutboundPoolName


@dataclass(frozen=True, slots=True)
class RelayClientWrapper:
    """One public typed RelayClient network wrapper."""

    method: str
    path: str
    http_method: RelayJsonMethod
    pool: OutboundPoolName


@dataclass(frozen=True, slots=True)
class OperatorForwardRoute:
    """One registered operator route that opens an upstream HTTP response."""

    path: str
    method: RelayJsonMethod
    pool: OutboundPoolName


@dataclass(frozen=True, slots=True)
class OperatorServerTerminatedRoute:
    """One registered relay route that does not use HTTP forwarding."""

    path: str
    method: RelayJsonMethod
    owner: ServerTerminatedOwner


RELAY_GENERIC_CALLS = (
    RelayGenericCall(
        "agents/evolution/agent.py",
        "find_spa_taxids",
        "get_json",
        "spa-faq/{}",
        OutboundPoolName.SPA_FAQ,
    ),
    RelayGenericCall(
        "agents/knowledge/retrieval.py",
        "_retrieve_scope_docs",
        "post_json",
        "retrieve/search",
        OutboundPoolName.RETRIEVAL,
    ),
    RelayGenericCall(
        "agents/knowledge/retrieval.py",
        "_rerank_batch",
        "post_json",
        "rerank/rank",
        OutboundPoolName.RERANK,
    ),
    RelayGenericCall(
        "agents/data/nl2sql.py",
        "_execute_nl2sql_via_relay",
        "post_json",
        "database/nl2sql",
        OutboundPoolName.NL2SQL,
    ),
    RelayGenericCall(
        "agents/shared/sql.py",
        "relay_bi_query",
        "post_json",
        "bi/query",
        OutboundPoolName.BI,
    ),
    RelayGenericCall(
        "agents/analyst/task_ops.py",
        "task_status",
        "get_json",
        "analysis/{}",
        OutboundPoolName.ANALYSIS_STATUS,
    ),
    RelayGenericCall(
        "agents/analyst/task_ops.py",
        "task_log",
        "get_json",
        "analysis/{}/logs",
        OutboundPoolName.ANALYSIS_STATUS,
    ),
    RelayGenericCall(
        "agents/analyst/task_ops.py",
        "task_delete",
        "post_json",
        "analysis/{}/terminate",
        OutboundPoolName.ANALYSIS_CONTROL,
    ),
    RelayGenericCall(
        "agents/analyst/graph.py",
        "AnalystGraphMixin._post_submit_job",
        "post_json",
        "analysis/tasks",
        OutboundPoolName.ANALYSIS_CONTROL,
    ),
    RelayGenericCall(
        "common/relay_client.py",
        "RelayClient.get_research_capabilities",
        "get_json",
        "capabilities",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayGenericCall(
        "common/relay_client.py",
        "RelayClient.resolve_research_objects",
        "post_json",
        "research-input/object-grants",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayGenericCall(
        "common/relay_client.py",
        "RelayClient.verify_research_objects",
        "post_json",
        "research-input/object-grants/verify",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayGenericCall(
        "common/relay_client.py",
        "RelayClient.revoke_research_objects",
        "post_json",
        "research-input/object-grants/revoke",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayGenericCall(
        "common/relay_client.py",
        "RelayClient.get_obs_list",
        "get_json",
        "obs/list",
        OutboundPoolName.OBS,
    ),
)


RELAY_CLIENT_WRAPPERS = (
    RelayClientWrapper(
        "get_research_capabilities",
        "capabilities",
        "GET",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayClientWrapper(
        "resolve_research_objects",
        "research-input/object-grants",
        "POST",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayClientWrapper(
        "verify_research_objects",
        "research-input/object-grants/verify",
        "POST",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayClientWrapper(
        "revoke_research_objects",
        "research-input/object-grants/revoke",
        "POST",
        OutboundPoolName.RELAY_CONTROL,
    ),
    RelayClientWrapper(
        "put_obs_object",
        "obs/object",
        "PUT",
        OutboundPoolName.OBS,
    ),
    RelayClientWrapper(
        "get_obs_object",
        "obs/object",
        "GET",
        OutboundPoolName.OBS,
    ),
    RelayClientWrapper(
        "get_obs_object_to_path",
        "obs/object",
        "GET",
        OutboundPoolName.OBS,
    ),
    RelayClientWrapper(
        "get_obs_list",
        "obs/list",
        "GET",
        OutboundPoolName.OBS,
    ),
    RelayClientWrapper(
        "put_obs_dir",
        "obs/dir",
        "PUT",
        OutboundPoolName.OBS,
    ),
)


OPERATOR_FORWARD_ROUTES = (
    OperatorForwardRoute(
        "/v1/relay/llm/chat/completions",
        "POST",
        OutboundPoolName.LLM,
    ),
    OperatorForwardRoute(
        "/v1/relay/coder/chat/completions",
        "POST",
        OutboundPoolName.LLM,
    ),
    OperatorForwardRoute(
        "/v1/relay/embed/embeddings",
        "POST",
        OutboundPoolName.LLM,
    ),
    OperatorForwardRoute(
        "/v1/relay/retrieve/search",
        "POST",
        OutboundPoolName.RETRIEVAL,
    ),
    OperatorForwardRoute(
        "/v1/relay/rerank/rank",
        "POST",
        OutboundPoolName.RERANK,
    ),
    OperatorForwardRoute(
        "/v1/relay/database/nl2sql",
        "POST",
        OutboundPoolName.NL2SQL,
    ),
    OperatorForwardRoute(
        "/v1/relay/analysis/tasks",
        "POST",
        OutboundPoolName.ANALYSIS_CONTROL,
    ),
    OperatorForwardRoute(
        "/v1/relay/analysis/{task_id}",
        "GET",
        OutboundPoolName.ANALYSIS_STATUS,
    ),
    OperatorForwardRoute(
        "/v1/relay/analysis/{task_id}/logs",
        "GET",
        OutboundPoolName.ANALYSIS_STATUS,
    ),
    OperatorForwardRoute(
        "/v1/relay/analysis/{task_id}/terminate",
        "POST",
        OutboundPoolName.ANALYSIS_CONTROL,
    ),
    OperatorForwardRoute(
        "/v1/relay/spa-faq/{repo_id}",
        "GET",
        OutboundPoolName.SPA_FAQ,
    ),
)


OPERATOR_SERVER_TERMINATED_ROUTES = (
    OperatorServerTerminatedRoute("/v1/relay/healthz", "GET", "local"),
    OperatorServerTerminatedRoute("/v1/relay/capabilities", "GET", "control"),
    OperatorServerTerminatedRoute(
        "/v1/relay/research-input/object-grants", "POST", "control"
    ),
    OperatorServerTerminatedRoute(
        "/v1/relay/research-input/object-grants/verify", "POST", "control"
    ),
    OperatorServerTerminatedRoute(
        "/v1/relay/research-input/object-grants/revoke", "POST", "control"
    ),
    OperatorServerTerminatedRoute("/v1/relay/bi/query", "POST", "bi"),
    OperatorServerTerminatedRoute("/v1/relay/obs/object", "PUT", "obs"),
    OperatorServerTerminatedRoute("/v1/relay/obs/object", "GET", "obs"),
    OperatorServerTerminatedRoute("/v1/relay/obs/list", "GET", "obs"),
    OperatorServerTerminatedRoute("/v1/relay/obs/dir", "PUT", "obs"),
)


__all__ = [
    "OPERATOR_FORWARD_ROUTES",
    "OPERATOR_SERVER_TERMINATED_ROUTES",
    "RELAY_CLIENT_WRAPPERS",
    "RELAY_GENERIC_CALLS",
    "OperatorForwardRoute",
    "OperatorServerTerminatedRoute",
    "RelayClientWrapper",
    "RelayGenericCall",
]
