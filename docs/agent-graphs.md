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

**Reused subgraphs at deeper `--xray`**: one shared chat subgraph is
mounted at several nesting positions (knowledge's generate node and
its retrieve worker each embed it; analyst and brief_gene add their
own chat node on top), so `--xray 2` / `--xray 3` expand more than one
`chat` block. Mermaid refuses two subgraphs that share a leaf name, so
the renderer suffixes the later occurrences (`chat` -> `chat_2`, ...)
for the Mermaid and PNG output only. The runtime graph and the
exported JSON manifests are untouched and keep the original single
`chat` node name.

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

The graph compiles into seven functional nodes (the `generate` and
`follow_up` calls are each split into a prep + post pair around one
shared `chat` subgraph node):

| Node                  | Role                                                                                                      |
| --------------------- | --------------------------------------------------------------------------------------------------------- |
| `process_files_node`  | Downloads attached OBS files and converts them into a bounded `upload_context` string.                    |
| `retrieve_node`       | Issues `retrieve` + `rerank` against the knowledge repos, populates `retrieved_docs`.                     |
| `generate_prep_node`  | Builds the `chat_payload` for the primary generate call and stages the `generate_post_node` sentinel.     |
| `generate_post_node`  | Merges the retrieved docs into the shared chat response and stores it in `final_response`.                |
| `follow_up_prep_node` | Builds the `chat_payload` for the follow-up questions call and stages the `follow_up_post_node` sentinel. |
| `follow_up_post_node` | Parses the follow-up questions and embeds them on the primary assistant message.                          |
| `chat`                | Mounted chat subgraph reused by both the generate and follow-up sites via the prep/post pairs.            |

Routing is conditional throughout: `__start__` branches to
`process_files_node` or `retrieve_node`, `retrieve_node` flows to
`generate_prep_node` or `__end__`, the shared `chat` node's after-router
returns to `generate_post_node` or `follow_up_post_node`, and
`generate_post_node` either flows into `follow_up_prep_node` or
short-circuits to `__end__`.

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

The graph compiles into seven functional nodes (the retrieve site is
mounted as a prep + post pair around a `knowledge` subgraph node, and
the rewrite site as a prep + post pair around the shared `chat` node):

| Node                 | Role                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------- |
| `retrieve_prep_node` | Stages the `knowledge_payload` and the post-knowledge sentinel; no retrieve call happens here.    |
| `knowledge`          | Mounted knowledge subgraph that issues the `retrieve` + `rerank` fan-out and writes its response. |
| `retrieve_post_node` | Formats the retrieved scenario fragments into the `retrieve_prompt` SQL-rewrite prompt.           |
| `rewrite_prep_node`  | Builds the `chat_payload` from the `retrieve_prompt`.                                             |
| `chat`               | Mounted chat subgraph that runs the single rewrite completion.                                    |
| `rewrite_post_node`  | Converts the chat response into the rewritten NL question stored on `rewrite_query`.              |
| `search_node`        | Executes the NL2SQL request through `nl2sql.execute_nl2sql_request` and stores the response dict. |

`__start__` routes conditionally to `retrieve_prep_node` (when
`is_rewrite`) or directly to `search_node`; the mounted `knowledge`
node's one-branch after-router returns to `retrieve_post_node`.

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

The graph compiles into 17 functional nodes wired with conditional
routing. Each of the five LLM call sites (`parse_query`, `data_select`,
`plan`, `check`, `tool_extract`) is split into a prep + post pair around
one shared `chat` subgraph node, and the `method_retrieve` site is a
prep + post pair around a mounted `knowledge` subgraph node:

| Node                        | Role                                                                                            |
| --------------------------- | ----------------------------------------------------------------------------------------------- |
| `parse_query_prep_node`     | Stages the chat payload for the parse-query call (or short-circuits when the goal is preset).   |
| `parse_query_post_node`     | Parses the parse-query chat response into goal, data list, and plan slots.                      |
| `data_select_prep_node`     | Stages the chat payload for the auto-selection call when `is_auto_select=True`.                 |
| `data_select_post_node`     | Parses the data-selection chat response into the selected data list.                            |
| `method_retrieve_prep_node` | Stages the knowledge input + post-knowledge sentinel for the method/SOP/literature lookup.      |
| `knowledge`                 | Mounted knowledge subgraph that runs the `retrieve` + `rerank` fan-out for the plan context.    |
| `method_retrieve_post_node` | Parses the knowledge response into the `method_context` delta.                                  |
| `plan_prep_node`            | Stages the chat payload for the plan-generation call.                                           |
| `plan_post_node`            | Parses the plan chat response into the analysis plan.                                           |
| `check_prep_node`           | Stages the chat payload for the plan-check call (or auto-approves a preset plan).               |
| `check_post_node`           | Parses the plan-check response and routes back to `plan_prep_node` until approved or capped.    |
| `tool_extract_prep_node`    | Stages the chat payload for the tool-extraction call.                                           |
| `tool_extract_post_node`    | Parses the tool-extraction response into the required tool list.                                |
| `tool_retrieve_node`        | Looks up tool usages for the extracted tools.                                                   |
| `submit_node`               | Submits the task to the computation platform and stores `task_id`.                              |
| `pooling_node`              | Polls task status until terminal when `is_polling=True`; short-circuits to `__end__` otherwise. |
| `chat`                      | Mounted chat subgraph reused by all five LLM call sites via the prep/post pairs.                |

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

The graph compiles into 10 functional nodes. The retrieve site is a
Send-dispatched prep/worker/reduce fan-out over the resolved gene
symbols, and the `generate` and `follow_up` calls are each split into a
prep + post pair around one shared `chat` subgraph node:

| Node                       | Role                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `query_judge_node`         | Decides whether the user query is a known gene ID; on hit routes to annotation fetch, else direct retrieval.    |
| `fetch_annotation_node`    | Pulls GO / KEGG / InterPro annotation strings from the BI endpoint for the resolved gene.                       |
| `retrieve_prep_tasks_node` | Builds the per-symbol task list and `Send`-dispatches one worker per task.                                      |
| `retrieve_worker_node`     | Per-symbol worker that invokes the mounted knowledge subgraph and writes an indexed `(task_index, docs)` tuple. |
| `retrieve_reduce_node`     | Merges, sorts, and caps the per-worker doc lists into `retrieved_docs` plus the `retrieve_context` string.      |
| `generate_prep_node`       | Builds the `chat_payload` from the annotation + retrieval context and stages the `generate_post_node` sentinel. |
| `generate_post_node`       | Merges the retrieved docs into the shared chat response and stores it in `final_response`.                      |
| `follow_up_prep_node`      | Builds the `chat_payload` for the follow-up questions call and stages the `follow_up_post_node` sentinel.       |
| `follow_up_post_node`      | Parses the follow-up questions and embeds them on the primary assistant message.                                |
| `chat`                     | Mounted chat subgraph reused by both the generate and follow-up sites via the prep/post pairs.                  |

Routing is conditional at three sites: `query_judge_node` branches to
`fetch_annotation_node` or straight to the retrieve fan-out,
`retrieve_prep_tasks_node` `Send`-fans out to `retrieve_worker_node`,
and the shared `chat` after-router returns to `generate_post_node` or
`follow_up_post_node` (with `generate_post_node` either flowing into
`follow_up_prep_node` or short-circuiting to `__end__`).

After `generate_post_node`, `route_after_generate` inspects
`state["is_follow_up"]` (default `True` inside `arun`) and either
flows into `follow_up_prep_node` or short-circuits to `END`. Direct
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

The graph compiles into 19 functional nodes. It is a Send-based
parallel fan-out, not a linear pipeline: the `retrieve`, `draft`,
`review_results`, and `revised` stages each run as a
dispatch → worker → reduce triple that `Send`-fans out one worker per
research dimension. The `plan_query`, `summary`, and `follow_up` LLM
calls are each split into a prep + post pair around one shared `chat`
subgraph node:

| Node                         | Role                                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------------------- |
| `plan_query_prep_node`       | Builds the plan-query `chat_payload` (with file-upload context) and stages the after-chat sentinel.      |
| `plan_query_post_node`       | Parses the plan-query response into the per-dimension `research_dimensions`.                             |
| `retrieve_dispatch`          | Split node that `Send`-fans out one retrieve worker per research dimension.                              |
| `retrieve_worker_node`       | Per-dimension worker that invokes the mounted knowledge subgraph and writes an indexed doc tuple.        |
| `retrieve_reduce_node`       | Merges the per-dimension docs into `all_raw_doc_list` and the `dimension_params`.                        |
| `draft_dispatch`             | Split node that `Send`-fans out one draft worker per dimension.                                          |
| `draft_worker_node`          | Per-dimension worker that drafts a review via the shared chat subgraph and writes an indexed tuple.      |
| `draft_reduce_node`          | Projects the indexed draft results into `draft_contents`.                                                |
| `review_results_dispatch`    | Split node that `Send`-fans out one critique worker per dimension.                                       |
| `review_results_worker_node` | Per-dimension worker that critiques a draft for accuracy / completeness via the shared chat subgraph.    |
| `review_results_reduce_node` | Projects the indexed critique results into `review_contents`.                                            |
| `revised_dispatch`           | Split node that `Send`-fans out one revision worker per dimension.                                       |
| `revised_worker_node`        | Per-dimension worker that revises a draft (via `_feedback_rag`) and may add supporting docs.             |
| `revised_reduce_node`        | Projects the indexed revised reports into `revised_reports`.                                             |
| `summary_prep_node`          | Builds the summary-synthesis `chat_payload` from the revised reports and stages the after-chat sentinel. |
| `summary_post_node`          | Parses the summary response into the `summary_content` markdown body.                                    |
| `follow_up_prep_node`        | Renumbers citations and builds the follow-up `chat_payload`.                                             |
| `follow_up_post_node`        | Assembles the `final_response` envelope from the renumbered text plus the follow-up list.                |
| `chat`                       | Mounted chat subgraph reused by the plan-query, summary, and follow-up sites via the prep/post pairs.    |

The four fan-out stages run sequentially
(`retrieve` → `draft` → `review_results` → `revised`); each
`dispatch` node uses a conditional `Send` router, and the shared `chat`
after-router branches back to `plan_query_post_node`,
`summary_post_node`, or `follow_up_post_node`. Parent graphs that want
to short-circuit the trailing summarisation should mount review via
`adapter_node` with an output mapper that ignores `summary_content`
rather than pinning a routing flag.

## Environment Subgraph

The regional VCI workflow is registered as `environment` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/environment/state.py`](../src/mcp_server_phytomni/agents/environment/state.py).
`region_vci_analysis` is now a thin wrapper that delegates to the
compiled subgraph via `ainvoke_graph`.

| TypedDict           | Required keys | Optional keys                                          |
| ------------------- | ------------- | ------------------------------------------------------ |
| `EnvironmentInput`  | `query`       | `batch`, `kwargs`                                      |
| `EnvironmentOutput` | —             | `vci_analysis_task`                                    |
| `EnvironmentState`  | `query`       | `batch`, `kwargs`, `region_codes`, `vci_analysis_task` |

The graph compiles into two nodes wired with one conditional edge:

| Node                        | Role                                                                                                                            |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `extract_region_codes_node` | Issues the chat extraction that parses `<result>province\|city\|county</result>` out of the user query.                         |
| `submit_vci_task_node`      | Builds the goal prompt + data list, materialises the run-scoped output directory, and submits the VCI task to the AnalystAgent. |

`route_after_extract` reads `state["region_codes"]`: a populated
list flows to `submit_vci_task_node`, a `None` value short-circuits
to `__end__`. Mirrors the legacy wrapper's early-return on
extraction failure so the wrapper still returns
`{"vci_analysis_task": None}` without invoking the analyst submit.

## Evolution Subgraph

The taxonomy-driven evolution workflow is registered as `evolution`
in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/evolution/state.py`](../src/mcp_server_phytomni/agents/evolution/state.py).
`evo_test_analysis` is now a thin wrapper that delegates to the
compiled subgraph via `ainvoke_graph`.

| TypedDict         | Required keys                 | Optional keys                                                                     |
| ----------------- | ----------------------------- | --------------------------------------------------------------------------------- |
| `EvolutionInput`  | `query`, `species`, `gene_id` | `batch`, `enable_auto_select`, `kwargs`                                           |
| `EvolutionOutput` | —                             | `evolution_agents_task`                                                           |
| `EvolutionState`  | `query`, `species`, `gene_id` | `batch`, `enable_auto_select`, `kwargs`, `target_taxids`, `evolution_agents_task` |

The graph compiles into two nodes wired with one conditional edge:

| Node                         | Role                                                                                                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `resolve_target_taxids_node` | Issues the chat extraction that parses target species names, then fans out per-species HTTP lookups to materialise the taxonomy ids.  |
| `submit_evolution_task_node` | Builds the goal prompt + data list, materialises the run-scoped output directory, and submits the evolution task to the AnalystAgent. |

`route_after_resolve` reads `state["target_taxids"]`: a populated
string (or the `"All"` sentinel) flows to
`submit_evolution_task_node`, a `None` value short-circuits to
`__end__`. The wrapper now returns the same
`{"evolution_agents_task": None}` key shape on both failure and
happy paths (previously the failure path returned the inconsistent
`{"evolution_task": None}` key).

## DeepGenome Subgraph

The DeepGenome gene-function workflow is registered as
`deep_genome` in
[`graphs.defaults.build_default_registry()`](../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app is the project's largest single graph: it
orchestrates a Part 1 knowledge profile (with a brief_gene mount
substituting for the legacy `_run_knowledge_agent`), a Part 2
parallel analyst fan-out across the deep analysis types, and a
Part 3 report synthesis chain.

The graph compiles into nine nodes:

| Node                 | Role                                                                                                                                                                                                                                     |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `knowledge_node`     | M11 brief_gene mount — fans out BI annotation + homology + interaction fetching that used to live in the deleted `data_node`, writes the four preamble sections.                                                                         |
| `prepare_tasks_node` | Materialises the per-gene analysis task list from the rewritten user query and the species code.                                                                                                                                         |
| `analyst_node`       | Send-dispatched worker: routes the three transferred analysis types (`evolution_analysis` / `protein_structure_analysis` / `promoter_analysis`) to module-level producer wrappers and the remaining 12 to `submit_analyst_via_subgraph`. |
| `synthesize_node`    | Barrier that waits for the analyst fan-out to drain, then triggers the experiment-loop.                                                                                                                                                  |
| `experiment_node`    | Loops over recommended experiments (also routes back to itself per Send) to keep building the experiment list.                                                                                                                           |
| `protocol_node`      | Calls `_dispatch_knowledge_retrieve` per experiment to retrieve protocol sections through the compiled knowledge subgraph.                                                                                                               |
| `discussion_node`    | Calls `_dispatch_chat` to generate the discussion section from the part-1 + part-2 content.                                                                                                                                              |
| `summary_node`       | Calls `_dispatch_chat` to generate the summary section from the introduction + part-1 + part-2 + part-4 stack.                                                                                                                           |
| `follow_up_node`     | Calls `_dispatch_chat` to generate the follow-up question list from the assembled final report.                                                                                                                                          |

The four `_dispatch_*` helpers on `DeepGenomeReportMixin` own the
chat / knowledge seams: `_dispatch_chat` (used by the experiment /
discussion / summary / follow-up nodes) routes through the compiled
chat subgraph; `_dispatch_knowledge_retrieve` (used by the protocol
node) routes through the compiled knowledge subgraph.
`_submit_analysis_task` on `DeepGenomeDispatchMixin` owns the
analyst-side seam, routing the three transferred analysis types to
their producer wrappers and the rest through
`submit_analyst_via_subgraph`.

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
analyst / brief_gene / chat / data / deep_genome / environment /
evolution / knowledge / review subgraphs (nine total). Snapshots
act as a visible contract for parent-graph authors and as
regression bait — any node-set or edge-set drift surfaces as a
diff in the same PR that causes it. A CI re-export-and-diff guard
remains pending. See [Declarative Graphs](#declarative-graphs) for
the reverse direction: reading a manifest back into a validated
view via `GraphLoader`.

## Declarative Graphs

`graphs/loader.py` ships a default-off loader that reads any
committed `graphs/manifests/*.graph.json` snapshot back into a
validated `GraphManifest` view. It is the read-back complement of
the `export_manifest()` write path and the foundation for future
LLM-authored or human-edited manifests that compile into LangGraph
apps.

**Feature flag.** `GRAPH_LOADER_ENABLED` on `ServerConfig` (env
`GRAPH_LOADER_ENABLED` or `PHYTOMNI_GRAPH_LOADER`) gates
construction; the default is `False`. With the flag off,
`GraphLoader()` raises `GraphLoaderDisabledError`, so import-time
exposure does not enable anything. Set
`PHYTOMNI_GRAPH_LOADER=true` per deployment to opt in.

**Allowlist.** `graphs/allowlist.py` derives
`default_subgraph_allowlist()` from
`build_default_registry().names()` — the same id set every other
registry consumer reads. The loader rejects any node whose `kind`
is `subgraph` and whose name is not in this set, so a manifest can
never reference a Python implementation outside the central
catalog. Adding a new agent to `build_default_registry()` lifts it
into the allowlist automatically.

**Schema.** `graphs/schema/graph_manifest.schema.json` is a static
JSON Schema export of `GraphManifest.model_json_schema()`. It is
the public contract LLM prompt assemblers, declarative graph
editors, and external jsonschema-based validators should read; the
loader test pins it equal to the Pydantic-generated schema so the
two cannot silently drift.

**Out of scope (today).** The loader produces a validated
structural view (nodes + edges + boundary classification); it does
**not** resolve `node_ref` to Python callables, **not** evaluate
inline route expressions, and **not** compile a runnable graph.
Future phases extend the loader to assemble compiled graphs from a
manifest; the safety constraint stays in `graphs/allowlist.py` so
node-ref and route-fn lookups inherit the same guard.

**Tour.** A flag-on smoke (e.g. in a Python REPL after setting
the env var) looks like:

```python
from mcp_server_phytomni.graphs.loader import GraphLoader

loader = GraphLoader()  # raises GraphLoaderDisabledError if flag is off
manifest = loader.load(
    "src/mcp_server_phytomni/graphs/manifests/chat.graph.json"
)
print(manifest.subgraph_node_names)
```

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
