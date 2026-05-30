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

## Knowledge Subgraph

The knowledge workflow is registered as `knowledge` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/knowledge/state.py`](../src/mcp_server_phytomni/agents/knowledge/state.py),
and the legacy `KnowledgeAgentState` symbol stays a back-compat
alias for `KnowledgeState` so internal node annotations remain
valid.

| TypedDict         | Required keys                      | Optional keys                                                  |
| ----------------- | ---------------------------------- | -------------------------------------------------------------- |
| `KnowledgeInput`  | `user_query`                       | `obs_file_list`, `repo_id_dict`, `is_generate`, `is_follow_up` |
| `KnowledgeOutput` | `retrieved_docs`, `final_response` | —                                                              |
| `KnowledgeState`  | every legacy field                 | (binary-compatible with `KnowledgeAgentState`)                 |

The graph compiles into four nodes:

| Node                 | Role                                                                                       |
| -------------------- | ------------------------------------------------------------------------------------------ |
| `process_files_node` | Downloads attached OBS files and converts them into a bounded `upload_context` string.     |
| `retrieve_node`      | Issues `retrieve` + `rerank` against the knowledge repos, populates `retrieved_docs`.      |
| `generate_node`      | Calls `phyto_chat` with the retrieval context and stores the answer in `final_response`.   |
| `follow_up_node`     | Runs a second LLM call for follow-up questions and merges them into the assistant message. |

## Data Subgraph

The NL2SQL workflow is registered as `data` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/data/state.py`](../src/mcp_server_phytomni/agents/data/state.py),
and the legacy `DataAgentState` symbol stays a back-compat alias
for `DataState`.

| TypedDict    | Required keys      | Optional keys                             |
| ------------ | ------------------ | ----------------------------------------- |
| `DataInput`  | `user_query`       | `is_rewrite`                              |
| `DataOutput` | `final_response`   | —                                         |
| `DataState`  | every legacy field | (binary-compatible with `DataAgentState`) |

The graph compiles into three nodes:

| Node            | Role                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------- |
| `retrieve_node` | Pulls scenario fragments from the data repo and stores a SQL-rewrite prompt.                      |
| `rewrite_node`  | Calls `phyto_chat` to convert the scenario prompt + user query into a rewritten NL question.      |
| `search_node`   | Executes the NL2SQL request through `nl2sql.execute_nl2sql_request` and stores the response dict. |

## Analyst Subgraph

The bioinformatics analysis workflow is registered as `analyst` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/analyst/state.py`](../src/mcp_server_phytomni/agents/analyst/state.py),
and the legacy `AnalystAgentsState` symbol stays a back-compat
alias for `AnalystState`.

| TypedDict       | Required keys      | Optional keys                                                                                                                                       |
| --------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AnalystInput`  | `query`            | `goal_description`, `preset_plan`, `data_list`, `obs_file_list`, `compute_resource`, `output_dir`, `is_polling`, `is_auto_select`, `is_preset_plan` |
| `AnalystOutput` | every output field | `surface_keys` + plan / tool / status + observability intermediates + `error_detail`                                                                |
| `AnalystState`  | every legacy field | (binary-compatible with `AnalystAgentsState`)                                                                                                       |

The graph compiles into nine nodes plus five conditional routers
(`parse_query` / `data_select` / `check` / `submit` / `pooling`):

| Node                   | Role                                                                                         |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| `parse_query_node`     | Decomposes the user query into goal, data list, and plan slots.                              |
| `data_select_node`     | Auto-selects data files from the available database when `is_auto_select=True`.              |
| `method_retrieve_node` | Retrieves methods, SOPs, and literature to build the plan context.                           |
| `plan_node`            | Generates or revises the analysis plan via `phyto_chat`.                                     |
| `check_node`           | Critic loop that validates the plan and routes back to `plan_node` until approved or capped. |
| `tool_extract_node`    | Extracts the required tools from the approved plan.                                          |
| `tool_retrieve_node`   | Looks up tool usages for the extracted tools.                                                |
| `submit_node`          | Submits the task to the computation platform and stores `task_id`.                           |
| `pooling_node`         | Polls task status until terminal when `is_polling=True`; short-circuits to END otherwise.    |

`graphs/analyst_dispatch_adapters.py` ships
`map_send_payload_to_analyst_input` and
`map_analyst_output_to_dispatch_state` for parent graphs (design /
network / research / deep_genome) that want to compose analyst via
`adapter_node` rather than mounting it directly.

## BriefGene Subgraph

The single-gene annotation + literature workflow is registered as
`brief_gene` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/brief_gene/state.py`](../src/mcp_server_phytomni/agents/brief_gene/state.py),
and the legacy `BriefGeneAgentState` symbol stays a back-compat
alias for `BriefGeneState`.

| TypedDict         | Required keys      | Optional keys                                                                                                                       |
| ----------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `BriefGeneInput`  | `user_query`       | `is_follow_up`                                                                                                                      |
| `BriefGeneOutput` | every output field | `gene_id`, `species_code`, `go_string`, `kegg_string`, `interpro_string`, `retrieved_docs`, `final_response`, `follow_up_questions` |
| `BriefGeneState`  | every legacy field | (binary-compatible with `BriefGeneAgentState`)                                                                                      |

The graph compiles into five nodes plus two conditional routers
(`query_judge` / `generate`):

| Node                    | Role                                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------ |
| `query_judge_node`      | Decides whether the user query is a known gene ID; on hit routes to annotation fetch, else direct retrieval. |
| `fetch_annotation_node` | Pulls GO / KEGG / InterPro annotation strings from the BI endpoint for the resolved gene.                    |
| `retrieve_node`         | Issues `retrieve` + `rerank` over the literature repos and stores `retrieved_docs`.                          |
| `generate_node`         | Calls `phyto_chat` with the annotation + retrieval context and stores the answer in `final_response`.        |
| `follow_up_node`        | Runs a second LLM call for follow-up questions and merges them into the assistant message.                   |

After `generate_node`, `route_after_generate` inspects
`state["is_follow_up"]` (default `True` inside `arun`) and either
flows into `follow_up_node` or short-circuits to `END`. Direct
callers via `BriefGeneAgent.arun` see the legacy follow-up
behavior; parent graphs mounting brief_gene as a subgraph may set
`is_follow_up=False` to skip the second LLM hop when they only
need the annotation + retrieval surface.

## Review Subgraph

The literature-deep-research workflow is registered as `review`
in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/review/state.py`](../src/mcp_server_phytomni/agents/review/state.py).
The legacy inline TypedDict is replaced by the same `DeepResearchState` symbol the file exports.

| TypedDict            | Required keys                       | Optional keys                                        |
| -------------------- | ----------------------------------- | ---------------------------------------------------- |
| `DeepResearchInput`  | `original_user_query`               | `obs_file_list`                                      |
| `DeepResearchOutput` | `final_response`, `summary_content` | —                                                    |
| `DeepResearchState`  | every legacy field                  | (binary-compatible with the legacy inline TypedDict) |

The graph compiles into seven nodes wired as a linear pipeline:

| Node                | Role                                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------------------- |
| `plan_node`         | Decomposes the user query into per-dimension research parameters and stores them in `dimension_params`. |
| `retrieve_node`     | Issues `retrieve` + `rerank` per dimension and merges the raw docs into `all_raw_doc_list`.             |
| `draft_node`        | Generates per-dimension draft reviews from the retrieved docs via `phyto_chat`.                         |
| `review_node`       | Reviews each draft for accuracy / completeness and stores the critic notes in `review_contents`.        |
| `revise_node`       | Revises drafts using the critic notes and may pull additional supporting docs into `add_doc_list`.      |
| `summary_node`      | Synthesizes the revised reports into the `summary_content` markdown body.                               |
| `post_process_node` | Wraps `summary_content` into a chat-completions-style `final_response` envelope for the HTTP API.       |

Unlike chat / brief_gene, the review subgraph carries no
conditional routers — every node runs in fixed order. Parent
graphs that want to short-circuit the trailing summarisation
should mount review via `adapter_node` with an output mapper that
ignores `summary_content` rather than pinning a routing flag.

## Nested Checkpoints

LangGraph's `parent.add_node("name", child_compiled_app)` pattern
shares the parent's `thread_id` through the `configurable` dict to
the embedded child. The spike in
[`tests/agents/test_nested_checkpoint_spike.py`](../tests/agents/test_nested_checkpoint_spike.py)
confirms three properties of the nested-persistence shape:

1. State keys shared between parent and child schemas project
   across the boundary automatically — the child's writes appear in
   the parent's final state without explicit merging.
1. The parent's `MemorySaver` records a checkpoint after the child
   completes, keyed by the parent's `thread_id`. The child's own
   checkpointer (if any) does not need to coordinate with the
   parent's saver — the parent owns resumability.
1. `app.aget_state(config)` on the parent under the same
   `thread_id` reads back the final aggregate state without
   re-invoking the graph, so the standard resumption seam works
   transparently when subgraphs are mounted.

Operational guidance: pass a single `thread_id` into the parent
graph and let LangGraph propagate it. Adapter-wrapped subgraphs
(`adapter_node`) inherit this shape because the adapter calls
`compiled_subgraph.ainvoke(state)` without overriding the
configurable layer.

## Manifest Snapshots

[`graphs/manifests/`](../src/mcp_server_phytomni/graphs/manifests/)
contains JSON snapshots produced by
`export_manifest(compiled_app).model_dump_json(indent=2)` for the
analyst / brief_gene / chat / data / knowledge / review subgraphs.
Snapshots act as a visible contract for parent-graph authors and
as regression bait — any node-set or edge-set drift surfaces as a
diff in the same PR that causes it. The `deep_genome` snapshot is
deferred until the upcoming DeepGenome composition work lands its
nested-subgraph topology. A CI re-export-and-diff guard remains
pending.

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
