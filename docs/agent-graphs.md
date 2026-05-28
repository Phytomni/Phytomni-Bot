# Agent Graphs

This document describes the graph composition layer that lives in
[`src/mcp_server_phytomni/graphs/`](../src/mcp_server_phytomni/graphs/)
and the visualization tooling that exposes it.

The layer exists so that one compiled LangGraph subgraph can be
loaded as a node inside another agent's parent graph (via
`parent.add_node("name", child_compiled_app)`), so that the resulting
nested structure is visible in `get_graph(xray=True).draw_mermaid()`
output, and so that future tooling can reason about graph structure
through a serializable manifest.

## Layer Pieces

| Symbol                          | Where                                                                 | Role                                                                                                                                                                                  |
| ------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SubgraphSpec`                  | [`graphs/spec.py`](../src/mcp_server_phytomni/graphs/spec.py)         | Frozen dataclass describing one cacheable subgraph: id, factory, optional state/input/output schemas, optional fingerprint fields.                                                    |
| `SubgraphRegistry`              | [`graphs/registry.py`](../src/mcp_server_phytomni/graphs/registry.py) | In-memory cache keyed on `(id, fingerprint)`. Calls the spec's factory at most once per key, reusing `runtime.langgraph_runner.config_fingerprint` so secrets never reach the key.    |
| `adapter_node`                  | [`graphs/adapters.py`](../src/mcp_server_phytomni/graphs/adapters.py) | Async parent-graph node wrapping a compiled subgraph: runs `map_in`, awaits `compiled_subgraph.ainvoke(...)`, runs `map_out`. Use when parent and child state schemas do not overlap. |
| `GraphManifest`                 | [`graphs/manifest.py`](../src/mcp_server_phytomni/graphs/manifest.py) | Pydantic snapshot of nodes (with `node` / `subgraph` / `boundary` classification) and edges (with `conditional` flag).                                                                |
| `export_manifest(compiled_app)` | [`graphs/manifest.py`](../src/mcp_server_phytomni/graphs/manifest.py) | Reflects a compiled LangGraph app into a `GraphManifest` snapshot. Read-only; loading a manifest back into a runtime graph is not yet supported.                                      |

## Two Registries, Two Roles

`SubgraphRegistry` and
[`runtime/langgraph_runner.py:GraphRegistry`](../src/mcp_server_phytomni/runtime/langgraph_runner.py)
have similar names and overlapping mechanics but solve different
problems. Treat them as orthogonal:

|                      | `GraphRegistry` (existing)                    | `SubgraphRegistry` (new)                                        |
| -------------------- | --------------------------------------------- | --------------------------------------------------------------- |
| Cache key            | `(agent name, full agent config fingerprint)` | `(subgraph id, narrow fingerprint)`                             |
| One entry represents | One complete agent's top-level compiled graph | One reusable sub-piece used by many parent graphs               |
| Created by           | `runtime.agent_registry.get_cached_agent`     | `SubgraphRegistry.get_or_compile`                               |
| Typical caller       | MCP handler bootstrapping an agent instance   | Parent graph attaching a child via `add_node` or `adapter_node` |

## Visualization Command

```bash
python scripts/visualize_agent_graphs.py            # Mermaid for all registered
python scripts/visualize_agent_graphs.py --list     # registered ids only
python scripts/visualize_agent_graphs.py --agent brief_gene
python scripts/visualize_agent_graphs.py --xray 2   # expand nested subgraphs
python scripts/visualize_agent_graphs.py --png  /tmp/g/   # also write PNGs
python scripts/visualize_agent_graphs.py --manifest /tmp/m/  # also write JSON manifests
```

The script installs the same offline fake-env vars the test suite
uses before any `mcp_server_phytomni` import, so it runs without a
real `.env`. Mermaid source goes to stdout; nothing is written
unless `--png` or `--manifest` is set.

**Privacy note**: `--png` calls LangGraph's `draw_mermaid_png()`
which posts the node/edge metadata to the public `mermaid.ink`
renderer. Node names and graph structure leave the host; no business
data or state values are transmitted. Skip `--png` if even node
names are sensitive.

## Chat Subgraph

The chat workflow is the first agent compiled as an atomic-Layer
subgraph. Its compiled app is registered as `chat` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py) so the visualization command renders it
alongside `brief_gene` and `deep_genome`.

| TypedDict    | Required keys | Optional keys                               | Where                                                              |
| ------------ | ------------- | ------------------------------------------- | ------------------------------------------------------------------ |
| `ChatInput`  | `user_query`  | `obs_file_list`, `chat_kwargs`              | [`chat/state.py`](../src/mcp_server_phytomni/agents/chat/state.py) |
| `ChatOutput` | `response`    | —                                           | [`chat/state.py`](../src/mcp_server_phytomni/agents/chat/state.py) |
| `ChatState`  | `user_query`  | every `ChatInput` key plus `upload_context` | [`chat/state.py`](../src/mcp_server_phytomni/agents/chat/state.py) |

The graph compiles into three nodes plus a conditional edge:

| Node                   | Role                                                                                         |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| `prepare_context_node` | Downloads attached OBS files and prepends the converted markdown to `user_query`.            |
| `generate_node`        | Issues the primary LLM completion via `_run_phyto_chat` so cache keys match the legacy path. |
| `follow_up_node`       | Runs a second LLM call for follow-up questions and embeds them on the assistant message.     |

After `generate_node`, `route_after_generate` inspects
`chat_kwargs["with_follow_up"]` (default `True`) and either flows
into `follow_up_node` or short-circuits to `END`. One compiled graph
therefore serves both the legacy no-follow `phyto_chat` shape and
the with-follow `phyto_chat_with_follow` shape via a single switch.

## Adding a New Subgraph

1. Define `Input` / `Output` / `State` TypedDicts in
   `agents/<domain>/state.py`.
1. Build the graph with
   `StateGraph(state_schema=State, input_schema=Input, output_schema=Output)` so parent graphs see a stable, narrow
   contract.
1. Register the compiled app with the project's central
   `SubgraphRegistry` (the registration site grows as Phases land).
1. Parent graphs load it either by `parent.add_node("name", subgraph_compiled_app)` (when state keys overlap) or by
   `parent.add_node("name", adapter_node(map_in, subgraph, map_out))`
   (when schemas need translation).
