# Task 6 History Consumption Report

## Scope

Implemented only the updated Task 6 history-consumption brief in the isolated
`Phytomni-Bot-five-sync` worktree at `2812605e`. Private bounded role-tagged
history now reaches the real Chat graph model input and the real
`KnowledgeAgent.arun` chat-subgraph input without changing public MCP/Pydantic
schemas, V0 arguments, streaming behavior, Go surfaces, docs, or stored
conversation payloads.

## Changes

- Added a private internal helper for normalizing bounded native-role history
  and building provider-ready message lists.
- Threaded `conversation_messages` through the Chat graph's opaque internal
  kwargs bag so the actual non-streaming model call receives `U1, A1, U2, A2`
  before the current user query.
- Threaded the same private history through `KnowledgeAgent` initial input and
  a request-local fallback around `arun`, then injected it into the mounted
  chat-subgraph payload used by the generate/follow-up prompt path.
- Added focused agent tests that patch the actual Chat model-call seam and the
  mounted Knowledge chat-subgraph seam, asserting the ordered history contract
  without exposing `conversation_messages` in public schemas.

## Verification

```text
uv run --no-sync pytest \
  tests/agents/test_conversation_context_consumption.py \
  tests/server/test_mcp_app_invoke.py \
  tests/server/test_query_route.py \
  tests/server/test_api_chat_completions.py \
  tests/server/test_conversation_context_routes.py -q
89 passed

uv run --no-sync black --check \
  src/mcp_server_phytomni/agents/shared/conversation_messages.py \
  src/mcp_server_phytomni/graphs/chat_adapters.py \
  src/mcp_server_phytomni/agents/chat/service.py \
  src/mcp_server_phytomni/agents/chat/graph.py \
  src/mcp_server_phytomni/agents/knowledge/state.py \
  src/mcp_server_phytomni/agents/knowledge/agent.py \
  tests/agents/test_conversation_context_consumption.py

uv run --no-sync ruff check \
  src/mcp_server_phytomni/agents/shared/conversation_messages.py \
  src/mcp_server_phytomni/graphs/chat_adapters.py \
  src/mcp_server_phytomni/agents/chat/service.py \
  src/mcp_server_phytomni/agents/chat/graph.py \
  src/mcp_server_phytomni/agents/knowledge/state.py \
  src/mcp_server_phytomni/agents/knowledge/agent.py \
  tests/agents/test_conversation_context_consumption.py
```
