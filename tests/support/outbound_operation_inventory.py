# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Finite expected inventories for outbound network operations."""

from __future__ import annotations

from collections import Counter
from typing import Literal

type _CallSite = tuple[str, str, str]

type _OperationDisposition = Literal[
    "pooled_http_adapter",
    "pooled_http_transport",
    "pooled_interop",
    "pooled_obs",
    "pooled_openai",
    "pooled_relay_stream",
    "separate_client_process",
]

_EXPECTED_NETWORK_OPERATIONS: Counter[_CallSite] = Counter(
    {
        (
            "mcp_server_phytomni/runtime/outbound/http.py",
            "BoundAsyncRequestClient.request",
            "self._profile_client.send",
        ): 1,
        (
            "mcp_server_phytomni/runtime/outbound/http.py",
            "BoundAsyncRequestClient.request",
            "response.aread",
        ): 1,
        (
            "mcp_server_phytomni/runtime/outbound/http.py",
            "OutboundHttpRuntime.stream",
            "self._profile_client(profile).stream",
        ): 1,
        (
            "mcp_server_phytomni/common/http.py",
            "_send_retry_request",
            "client.request",
        ): 1,
        (
            "mcp_server_phytomni/agents/evolution/agent.py",
            "find_spa_taxids",
            "client.request",
        ): 1,
        (
            "mcp_server_phytomni/common/relay_client.py",
            "RelayClient.get_obs_object_to_path",
            "http_runtime.stream",
        ): 1,
        (
            "mcp_server_phytomni/common/relay_client.py",
            "RelayClient.get_obs_object_to_path",
            "response.aiter_bytes",
        ): 1,
        (
            "mcp_server_phytomni/api/relay/forward.py",
            "_open_relay_upstream",
            "http_runtime.stream",
        ): 1,
        (
            "mcp_server_phytomni/api/relay/forward.py",
            "_buffered_relay_response",
            "upstream.aread",
        ): 1,
        (
            "mcp_server_phytomni/api/relay/forward.py",
            "_stream",
            "upstream.aiter_bytes",
        ): 1,
        (
            "mcp_server_phytomni/agents/chat/service.py",
            "_run_chat_completion_cached",
            "runtime.openai.chat.completions.create",
        ): 1,
        (
            "mcp_server_phytomni/agents/chat/service.py",
            "stream_phyto_chat_chunks",
            "runtime.openai.chat.completions.create",
        ): 1,
        (
            "mcp_server_phytomni/agents/expert/router.py",
            "_create",
            "runtime.openai.chat.completions.create",
        ): 1,
        (
            "mcp_server_phytomni/storage/obs_relay_ops.py",
            "head_object_metadata",
            "client.getObjectMetadata",
        ): 1,
        (
            "mcp_server_phytomni/storage/obs_relay_ops.py",
            "_iter_sdk_chunks",
            "sdk_client.getObject",
        ): 1,
        (
            "mcp_server_phytomni/storage/gene_example_reader.py",
            "_sdk_body",
            "client.getObject",
        ): 1,
        (
            "mcp_server_phytomni/storage/obs_relay_ops.py",
            "list_object_keys_page",
            "_resolve_client(resolved_access.client).listObjects",
        ): 1,
        (
            "mcp_server_phytomni/storage/obs_relay_ops.py",
            "_sdk",
            "_resolve_client(resolved_access.client).putContent",
        ): 3,
        (
            "mcp_server_phytomni/storage/obs_relay_ops.py",
            "_sdk",
            "sdk_client.getObjectMetadata",
        ): 1,
        (
            "mcp_server_phytomni/storage/multipart.py",
            "BoundedMultipartStorage.begin",
            "client.initiateMultipartUpload",
        ): 1,
        (
            "mcp_server_phytomni/storage/multipart.py",
            "BoundedMultipartStorage.put_part",
            "client.uploadPart",
        ): 1,
        (
            "mcp_server_phytomni/storage/multipart.py",
            "BoundedMultipartStorage.complete",
            "client.completeMultipartUpload",
        ): 1,
        (
            "mcp_server_phytomni/storage/multipart.py",
            "BoundedMultipartStorage.abort",
            "client.abortMultipartUpload",
        ): 1,
        (
            "mcp_server_phytomni/storage/multipart.py",
            "BoundedMultipartStorage.reconcile_complete",
            "client.getObjectMetadata",
        ): 1,
        (
            "mcp_server_phytomni/agents/shared/analysis_storage.py",
            "_create_output_dir_sdk",
            "obs_client.putContent",
        ): 1,
        (
            "mcp_server_phytomni/agents/analyst/storage.py",
            "_upload_content_sdk",
            "obs_client.putContent",
        ): 1,
        (
            "mcp_server_phytomni/agents/analyst/storage.py",
            "_delete_analyst_data_sdk",
            "obs_client.deleteObject",
        ): 1,
        (
            "mcp_server_phytomni/agents/analyst/storage.py",
            "_list_obs_object_keys_page",
            "obs_client.listObjects",
        ): 1,
        (
            "mcp_server_phytomni/agents/analyst/storage.py",
            "_download_obs_object",
            "obs_client.getObject",
        ): 1,
        (
            "mcp_server_phytomni/interop/http_transport.py",
            "InteropHTTPTransport.handle_async_request",
            "self._delegate.handle_async_request",
        ): 1,
        (
            "mcp_server_phytomni/interop/mcp_client.py",
            "_LeasedMcpSession.list_tools",
            "session.list_tools",
        ): 1,
        (
            "mcp_server_phytomni/interop/mcp_client.py",
            "_LeasedMcpSession.call_tool",
            "session.call_tool",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_client.py",
            "_open_execution",
            "sdk_client.send_message",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_client.py",
            "stream_with_client",
            "send_message",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_discovery.py",
            "_read_card_from_client",
            "client.get",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_discovery.py",
            "_read_card_from_client",
            "response.aread",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_discovery.py",
            "_read_card_payload",
            "client.get",
        ): 1,
        (
            "mcp_server_phytomni/interop/a2a_discovery.py",
            "_read_card_payload",
            "response.aread",
        ): 1,
        (
            "mcp_server_phytomni/interop/runtime.py",
            "InteropResourceRuntime._build_mcp_resource",
            "session.initialize",
        ): 1,
        (
            "mcp_client_phytomni/http_client.py",
            "PhytomniHttpClient._request",
            "client.request",
        ): 1,
        (
            "mcp_client_phytomni/client.py",
            "PhytomniMcpClient.connect",
            "self.session.initialize",
        ): 1,
        (
            "mcp_client_phytomni/client.py",
            "PhytomniMcpClient.list_tools",
            "session.list_tools",
        ): 1,
        (
            "mcp_client_phytomni/client.py",
            "PhytomniMcpClient.call_tool",
            "session.call_tool",
        ): 1,
        (
            "mcp_client_phytomni/client.py",
            "PhytomniToolRouter.route_query",
            "self.openai_client.chat.completions.create",
        ): 1,
    }
)

_EXPECTED_NON_NETWORK_OPERATIONS: Counter[_CallSite] = Counter(
    {
        (
            "mcp_client_phytomni/client.py",
            "PhytomniToolRouter.route_query",
            "self.mcp_client.call_tool",
        ): 1,
        (
            "mcp_client_phytomni/main.py",
            "_main",
            "client.call_tool",
        ): 1,
        (
            "mcp_client_phytomni/main.py",
            "_main",
            "client.list_tools",
        ): 1,
        (
            "mcp_server_phytomni/api/a2ui_limits.py",
            "_read_bounded_body",
            "request.stream",
        ): 1,
        (
            "mcp_server_phytomni/api/relay/deps.py",
            "read_relay_body",
            "request.stream",
        ): 1,
        (
            "mcp_server_phytomni/api/routes/uploads.py",
            "_request_part_body",
            "request.stream",
        ): 1,
        (
            "mcp_server_phytomni/mcp/app.py",
            "call_tool",
            "server.call_tool",
        ): 1,
        (
            "mcp_server_phytomni/mcp/app.py",
            "list_tools",
            "server.list_tools",
        ): 1,
    }
)

_NETWORK_OPERATION_DISPOSITIONS: dict[_CallSite, _OperationDisposition] = {
    site: (
        "separate_client_process"
        if site[0].startswith("mcp_client_phytomni/")
        else (
            "pooled_obs"
            if any(
                marker in site[2]
                for marker in (
                    "getObject",
                    "deleteObject",
                    "initiateMultipartUpload",
                    "listObjects",
                    "putContent",
                    "uploadPart",
                    "completeMultipartUpload",
                    "abortMultipartUpload",
                )
            )
            else (
                "pooled_openai"
                if ".openai." in site[2]
                else (
                    "pooled_interop"
                    if "/interop/" in site[0]
                    else (
                        "pooled_relay_stream"
                        if site[0]
                        in {
                            "mcp_server_phytomni/api/relay/forward.py",
                            "mcp_server_phytomni/common/relay_client.py",
                        }
                        else (
                            "pooled_http_transport"
                            if site[0]
                            == "mcp_server_phytomni/runtime/outbound/http.py"
                            else "pooled_http_adapter"
                        )
                    )
                )
            )
        )
    )
    for site in _EXPECTED_NETWORK_OPERATIONS
}
