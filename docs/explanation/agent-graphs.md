# Agent Graphs

This document describes the graph composition layer that lives in
[`src/mcp_server_phytomni/graphs/`](../../src/mcp_server_phytomni/graphs/)
and the visualization tooling that exposes it.

The layer exists so that one compiled LangGraph subgraph can be
loaded as a node inside another agent's parent graph (via
`parent.add_node("name", child_compiled_app)`), so that the resulting
nested structure is visible in `get_graph(xray=True).draw_mermaid()`
output, and so that future tooling can reason about graph structure
through a serializable manifest.

## Layer Pieces

- **Symbol:** `SubgraphSpec`
  **Where:** [`graphs/spec.py`](../../src/mcp_server_phytomni/graphs/spec.py)
  **Role:** Frozen dataclass describing one cacheable subgraph: id, factory,
  optional state/input/output schemas, optional fingerprint fields.

- **Symbol:** `SubgraphRegistry`
  **Where:**
  [`graphs/registry.py`](../../src/mcp_server_phytomni/graphs/registry.py)
  **Role:** In-memory cache keyed on `(id, fingerprint)`. Calls the spec's
  factory at most once per key, reusing
  `runtime.langgraph_runner.config_fingerprint` so secrets never reach
  the key.

- **Symbol:** `adapter_node`
  **Where:**
  [`graphs/adapters.py`](../../src/mcp_server_phytomni/graphs/adapters.py)
  **Role:** Async parent-graph node wrapping a compiled subgraph: runs `map_in`,
  awaits `compiled_subgraph.ainvoke(...)`, runs `map_out`. Use when
  parent and
  child state schemas do not overlap.

- **Symbol:** `GraphManifest`
  **Where:**
  [`graphs/manifest.py`](../../src/mcp_server_phytomni/graphs/manifest.py)
  **Role:** Pydantic snapshot of nodes (with `node` / `subgraph` / `boundary`
  classification) and edges (with `conditional` flag).

- **Symbol:** `export_manifest(compiled_app)`
  **Where:**
  [`graphs/manifest.py`](../../src/mcp_server_phytomni/graphs/manifest.py)
  **Role:** Reflects a compiled LangGraph app into a `GraphManifest` snapshot.
  Read-only; loading a manifest back into a runtime graph is not yet
  supported.

## Two Registries, Two Roles

`SubgraphRegistry` and
[`runtime/langgraph_runner.py:GraphRegistry`](../../src/mcp_server_phytomni/runtime/langgraph_runner.py)
have similar names and overlapping mechanics but solve different
problems. Treat them as orthogonal:

- **:** Cache key
  **`GraphRegistry` (existing):** `(agent name, full agent config fingerprint)`
  **`SubgraphRegistry` (new):** `(subgraph id, narrow fingerprint)`

- **:** One entry represents
  **`GraphRegistry` (existing):** One complete agent's top-level compiled graph
  **`SubgraphRegistry` (new):** One reusable sub-piece used by many parent
  graphs

- **:** Created by
  **`GraphRegistry` (existing):** `runtime.agent_registry.get_cached_agent`
  **`SubgraphRegistry` (new):** `SubgraphRegistry.get_or_compile`

- **:** Typical caller
  **`GraphRegistry` (existing):** MCP handler bootstrapping an agent instance
  **`SubgraphRegistry` (new):** Parent graph attaching a child via `add_node` or
  `adapter_node`

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
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py)
so the visualization command renders it
alongside `brief_gene` and `deep_genome`.

- **TypedDict:** `ChatInput`
  **Required keys:** `user_query`
  **Optional keys:** `obs_file_list`, `chat_kwargs`
  **Where:**
  [`chat/state.py`](../../src/mcp_server_phytomni/agents/chat/state.py)

- **TypedDict:** `ChatOutput`
  **Required keys:** `response`
  **Optional keys:** —
  **Where:**
  [`chat/state.py`](../../src/mcp_server_phytomni/agents/chat/state.py)

- **TypedDict:** `ChatState`
  **Required keys:** `user_query`
  **Optional keys:** every `ChatInput` key plus `upload_context`
  **Where:**
  [`chat/state.py`](../../src/mcp_server_phytomni/agents/chat/state.py)

The graph compiles into three nodes plus a conditional edge:

- **Node:** `prepare_context_node`
  **Role:** Downloads attached OBS files and prepends the converted markdown to
  `user_query`.

- **Node:** `generate_node`
  **Role:** Issues the primary LLM completion via `_run_phyto_chat` so cache
  keys match the legacy path.

- **Node:** `follow_up_node`
  **Role:** Runs a second LLM call for follow-up questions and embeds them on
  the assistant message.

After `generate_node`, `route_after_generate` inspects
`chat_kwargs["with_follow_up"]` (default `True`) and either flows
into `follow_up_node` or short-circuits to `END`. One compiled graph
therefore serves both the legacy no-follow `phyto_chat` shape and
the with-follow `phyto_chat_with_follow` shape via a single switch.

## Knowledge Subgraph

The knowledge workflow is registered as `knowledge` in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/knowledge/state.py`](../../src/mcp_server_phytomni/agents/knowledge/state.py),
and the legacy `KnowledgeAgentState` symbol stays a back-compat
alias for `KnowledgeState` so internal node annotations remain
valid.

- **TypedDict:** `KnowledgeInput`
  **Required keys:** `user_query`
  **Optional keys:** `obs_file_list`, `repo_id_dict`, `is_generate`,
  `is_follow_up`

- **TypedDict:** `KnowledgeOutput`
  **Required keys:** `retrieved_docs`, `final_response`
  **Optional keys:** —

- **TypedDict:** `KnowledgeState`
  **Required keys:** every legacy field
  **Optional keys:** (binary-compatible with `KnowledgeAgentState`)

The graph compiles into seven functional nodes (the `generate` and
`follow_up` calls are each split into a prep + post pair around one
shared `chat` subgraph node):

- **Node:** `process_files_node`
  **Role:** Downloads attached OBS files and converts them into a bounded
  `upload_context` string.

- **Node:** `retrieve_node`
  **Role:** Issues `retrieve` + `rerank` against the knowledge repos, populates
  `retrieved_docs`.

- **Node:** `generate_prep_node`
  **Role:** Builds the `chat_payload` for the primary generate call and stages
  the `generate_post_node` sentinel.

- **Node:** `generate_post_node`
  **Role:** Merges the retrieved docs into the shared chat response and stores
  it in `final_response`.

- **Node:** `follow_up_prep_node`
  **Role:** Builds the `chat_payload` for the follow-up questions call and
  stages the `follow_up_post_node` sentinel.

- **Node:** `follow_up_post_node`
  **Role:** Parses the follow-up questions and embeds them on the primary
  assistant message.

- **Node:** `chat`
  **Role:** Mounted chat subgraph reused by both the generate and follow-up
  sites via the prep/post pairs.

Routing is conditional throughout: `__start__` branches to
`process_files_node` or `retrieve_node`, `retrieve_node` flows to
`generate_prep_node` or `__end__`, the shared `chat` node's after-router
returns to `generate_post_node` or `follow_up_post_node`, and
`generate_post_node` either flows into `follow_up_prep_node` or
short-circuits to `__end__`.

## Data Subgraph

The NL2SQL workflow is registered as `data` in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/data/state.py`](../../src/mcp_server_phytomni/agents/data/state.py),
and the legacy `DataAgentState` symbol stays a back-compat alias
for `DataState`.

- **TypedDict:** `DataInput`
  **Required keys:** `user_query`
  **Optional keys:** `is_rewrite`

- **TypedDict:** `DataOutput`
  **Required keys:** `final_response`
  **Optional keys:** —

- **TypedDict:** `DataState`
  **Required keys:** every legacy field
  **Optional keys:** (binary-compatible with `DataAgentState`)

The graph compiles into seven functional nodes (the retrieve site is
mounted as a prep + post pair around a `knowledge` subgraph node, and
the rewrite site as a prep + post pair around the shared `chat` node):

- **Node:** `retrieve_prep_node`
  **Role:** Stages the `knowledge_payload` and the post-knowledge sentinel; no
  retrieve call happens here.

- **Node:** `knowledge`
  **Role:** Mounted knowledge subgraph that issues the `retrieve` + `rerank`
  fan-out and writes its response.

- **Node:** `retrieve_post_node`
  **Role:** Formats the retrieved scenario fragments into the `retrieve_prompt`
  SQL-rewrite prompt.

- **Node:** `rewrite_prep_node`
  **Role:** Builds the `chat_payload` from the `retrieve_prompt`.

- **Node:** `chat`
  **Role:** Mounted chat subgraph that runs the single rewrite completion.

- **Node:** `rewrite_post_node`
  **Role:** Converts the chat response into the rewritten NL question stored on
  `rewrite_query`.

- **Node:** `search_node`
  **Role:** Executes the NL2SQL request through `nl2sql.execute_nl2sql_request`
  and stores the response dict.

`__start__` routes conditionally to `retrieve_prep_node` (when
`is_rewrite`) or directly to `search_node`; the mounted `knowledge`
node's one-branch after-router returns to `retrieve_post_node`.

## Analyst Subgraph

The bioinformatics analysis workflow is registered as `analyst` in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/analyst/state.py`](../../src/mcp_server_phytomni/agents/analyst/state.py),
and the legacy `AnalystAgentsState` symbol stays a back-compat
alias for `AnalystState`.

- **TypedDict:** `AnalystInput`
  **Required keys:** `query`
  **Optional keys:** `goal_description`, `preset_plan`, `data_list`,
  `obs_file_list`, `compute_resource`, `output_dir`,
  `is_polling`,
  `is_auto_select`, `is_preset_plan`

- **TypedDict:** `AnalystOutput`
  **Required keys:** every output field
  **Optional keys:** `surface_keys` + plan / tool / status + observability
  intermediates + `error_detail`

- **TypedDict:** `AnalystState`
  **Required keys:** every legacy field
  **Optional keys:** (binary-compatible with `AnalystAgentsState`)

The graph compiles into 17 functional nodes wired with conditional
routing. Each of the five LLM call sites (`parse_query`, `data_select`,
`plan`, `check`, `tool_extract`) is split into a prep + post pair around
one shared `chat` subgraph node, and the `method_retrieve` site is a
prep + post pair around a mounted `knowledge` subgraph node:

- **Node:** `parse_query_prep_node`
  **Role:** Stages the chat payload for the parse-query call (or short-circuits
  when the goal is preset).

- **Node:** `parse_query_post_node`
  **Role:** Parses the parse-query chat response into goal, data list, and plan
  slots.

- **Node:** `data_select_prep_node`
  **Role:** Stages the chat payload for the auto-selection call when
  `is_auto_select=True`.

- **Node:** `data_select_post_node`
  **Role:** Parses the data-selection chat response into the selected data list.

- **Node:** `method_retrieve_prep_node`
  **Role:** Stages the knowledge input + post-knowledge sentinel for the
  method/SOP/literature lookup.

- **Node:** `knowledge`
  **Role:** Mounted knowledge subgraph that runs the `retrieve` + `rerank`
  fan-out for the plan context.

- **Node:** `method_retrieve_post_node`
  **Role:** Parses the knowledge response into the `method_context` delta.

- **Node:** `plan_prep_node`
  **Role:** Stages the chat payload for the plan-generation call.

- **Node:** `plan_post_node`
  **Role:** Parses the plan chat response into the analysis plan.

- **Node:** `check_prep_node`
  **Role:** Stages the chat payload for the plan-check call (or auto-approves a
  preset plan).

- **Node:** `check_post_node`
  **Role:** Parses the plan-check response and routes back to `plan_prep_node`
  until approved or capped.

- **Node:** `tool_extract_prep_node`
  **Role:** Stages the chat payload for the tool-extraction call.

- **Node:** `tool_extract_post_node`
  **Role:** Parses the tool-extraction response into the required tool list.

- **Node:** `tool_retrieve_node`
  **Role:** Looks up tool usages for the extracted tools.

- **Node:** `submit_node`
  **Role:** Submits the task to the computation platform and stores `task_id`.

- **Node:** `pooling_node`
  **Role:** Polls task status until terminal when `is_polling=True`;
  short-circuits to `__end__` otherwise.

- **Node:** `chat`
  **Role:** Mounted chat subgraph reused by all five LLM call sites via the
  prep/post pairs.

`graphs/analyst_dispatch_adapters.py` ships
`map_send_payload_to_analyst_input` and
`map_analyst_output_to_dispatch_state` for parent graphs (design /
network / research / deep_genome) that want to compose analyst via
`adapter_node` rather than mounting it directly.

## BriefGene Subgraph

The single-gene annotation + literature workflow is registered as
`brief_gene` in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/brief_gene/state.py`](../../src/mcp_server_phytomni/agents/brief_gene/state.py),
and the legacy `BriefGeneAgentState` symbol stays a back-compat
alias for `BriefGeneState`.

- **TypedDict:** `BriefGeneInput`
  **Required keys:** `user_query`
  **Optional keys:** `is_follow_up`

- **TypedDict:** `BriefGeneOutput`
  **Required keys:** every output field (annotation strings, three homology
  dicts, `section{1-4}_markdown`, `introduction_report`,
  `retrieved_docs`,
  `final_response`, `follow_up_questions`)
  **Optional keys:** — (total `TypedDict`; `deep_genome` consumes
  `final_response` verbatim)

- **TypedDict:** `BriefGeneState`
  **Required keys:** every legacy field
  **Optional keys:** (binary-compatible with `BriefGeneAgentState`)

The graph compiles into fourteen functional nodes plus a mounted
`chat` subgraph. The gene profile is built by a **static fan-out**
(not `Send`): `query_judge_node` fans unconditionally to
`fetch_homology_interactions_node` and conditionally to
`fetch_annotation_node` (or straight to retrieval); the four `section_*`
nodes gate on the single `retrieve_reduce_node` trigger and read the
homology counts from state. The retrieve site is the only
`Send`-dispatched fan-out (prep → worker × N → reduce) over the resolved
gene symbols, and `chat` is now reused only by the follow-up pair:

- **Node:** `query_judge_node`
  **Role:** Resolves whether the user query is a known gene ID; on hit routes to
  annotation fetch, else straight to retrieval.

- **Node:** `fetch_annotation_node`
  **Role:** Pulls GO / KEGG / InterPro / description annotation strings from the
  BI endpoint for the resolved gene.

- **Node:** `fetch_homology_interactions_node`
  **Role:** Runs unconditionally off `query_judge_node`; fetches ortholog /
  paralog / interaction rows and commits their count summaries to
  state in an
  early superstep.

- **Node:** `retrieve_prep_tasks_node`
  **Role:** Builds the per-symbol task list and `Send`-dispatches one worker per
  task.

- **Node:** `retrieve_worker_node`
  **Role:** Per-symbol worker that invokes the mounted knowledge subgraph and
  writes an indexed `(task_index, docs)` tuple. On a recovered
  per-symbol retrieve
  fault its broad `except` keeps the empty `(task_index, [])` sentinel
  and appends
  a `DegradedRecord` to the status-independent `literature_degraded`
  channel
  (never `failures`), so the run stays SUCCESS.

- **Node:** `retrieve_reduce_node`
  **Role:** Merges, sorts, and caps the per-worker doc lists into
  `retrieved_docs` plus the `retrieve_context` string.

- **Node:** `section_discovery_node`
  **Role:** LLM-writes the `### 1.` Gene Discovery section from annotation +
  literature + homology state.

- **Node:** `section_cloning_node`
  **Role:** LLM-writes the `### 2.` Gene Cloning section.

- **Node:** `section_functional_node`
  **Role:** LLM-writes the `### 3.` Functional Analysis section.

- **Node:** `section_application_node`
  **Role:** LLM-writes the `### 4.` Application and Evolutionary Analysis
  section.

- **Node:** `introduction_node`
  **Role:** LLM-writes the introduction report from the Basic Information block
  and the four section markdowns.

- **Node:** `render_node`
  **Role:** Pure-template node that assembles the title + introduction +
  `## Gene Profiles` preamble and writes `final_response`. When
  `literature_degraded`
  is non-empty it prepends a `⚠️ Literature retrieval degraded` banner
  between the
  H1 and the introduction (empty string on the happy path so the
  deep_genome
  verbatim mount stays byte-identical).

- **Node:** `follow_up_prep_node`
  **Role:** Builds the `chat_payload` for the follow-up questions call (only
  when `is_follow_up`) and stages the post sentinel.

- **Node:** `follow_up_post_node`
  **Role:** Parses the follow-up questions and embeds them on the primary
  assistant message.

- **Node:** `chat`
  **Role:** Mounted chat subgraph; now reused only by the follow-up site via the
  prep/post pair.

Routing: `query_judge_node` unconditionally edges to
`fetch_homology_interactions_node` and conditionally (`route_after_judge`)
to `fetch_annotation_node` or the retrieve fan-out;
`retrieve_prep_tasks_node` `Send`-fans out to `retrieve_worker_node`;
each `section_*` node plain-edges to `introduction_node` (the four-way
fan-in converges on LangGraph's superstep barrier — there is no explicit
barrier router); and `introduction_node` edges to `render_node`.

After `render_node`, `route_after_generate` inspects
`state["is_follow_up"]` (default `True` inside `arun`) and either flows
into `follow_up_prep_node` → `chat` → `follow_up_post_node` or
short-circuits to `END`. Direct callers via `BriefGeneAgent.arun` see
the legacy follow-up behavior; a parent graph mounting brief_gene as a
subgraph sets `is_follow_up=False` to skip the follow-up hop. `deep_genome`
mounts the compiled app and consumes `render_node`'s rendered answer
verbatim as its report preamble (only the H1 title is swapped).

## Review Subgraph

The literature-deep-research workflow is registered as `review`
in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/review/state.py`](../../src/mcp_server_phytomni/agents/review/state.py).
The legacy inline TypedDict is replaced by the same `DeepResearchState` symbol
the file exports.

- **TypedDict:** `DeepResearchInput`
  **Required keys:** `original_user_query`
  **Optional keys:** `obs_file_list`

- **TypedDict:** `DeepResearchOutput`
  **Required keys:** `final_response`, `summary_content`
  **Optional keys:** —

- **TypedDict:** `DeepResearchState`
  **Required keys:** every legacy field
  **Optional keys:** (binary-compatible with the legacy inline TypedDict)

The graph compiles into 19 functional nodes. It is a Send-based
parallel fan-out, not a linear pipeline: the `retrieve`, `draft`,
`review_results`, and `revised` stages each run as a
dispatch → worker → reduce triple that `Send`-fans out one worker per
research dimension. The `plan_query`, `summary`, and `follow_up` LLM
calls are each split into a prep + post pair around one shared `chat`
subgraph node:

- **Node:** `plan_query_prep_node`
  **Role:** Builds the plan-query `chat_payload` (with file-upload context) and
  stages the after-chat sentinel.

- **Node:** `plan_query_post_node`
  **Role:** Parses the plan-query response into the per-dimension
  `research_dimensions`.

- **Node:** `retrieve_dispatch`
  **Role:** Split node that `Send`-fans out one retrieve worker per research
  dimension.

- **Node:** `retrieve_worker_node`
  **Role:** Per-dimension worker that invokes the mounted knowledge subgraph and
  writes an indexed doc tuple.

- **Node:** `retrieve_reduce_node`
  **Role:** Merges the per-dimension docs into `all_raw_doc_list` and the
  `dimension_params`.

- **Node:** `draft_dispatch`
  **Role:** Split node that `Send`-fans out one draft worker per dimension.

- **Node:** `draft_worker_node`
  **Role:** Per-dimension worker that drafts a review via the shared chat
  subgraph and writes an indexed tuple.

- **Node:** `draft_reduce_node`
  **Role:** Projects the indexed draft results into `draft_contents`.

- **Node:** `review_results_dispatch`
  **Role:** Split node that `Send`-fans out one critique worker per dimension.

- **Node:** `review_results_worker_node`
  **Role:** Per-dimension worker that critiques a draft for accuracy /
  completeness via the shared chat subgraph.

- **Node:** `review_results_reduce_node`
  **Role:** Projects the indexed critique results into `review_contents`.

- **Node:** `revised_dispatch`
  **Role:** Split node that `Send`-fans out one revision worker per dimension.

- **Node:** `revised_worker_node`
  **Role:** Per-dimension worker that revises a draft (via `_feedback_rag`) and
  may add supporting docs.

- **Node:** `revised_reduce_node`
  **Role:** Projects the indexed revised reports into `revised_reports`.

- **Node:** `summary_prep_node`
  **Role:** Builds the summary-synthesis `chat_payload` from the revised reports
  and stages the after-chat sentinel.

- **Node:** `summary_post_node`
  **Role:** Parses the summary response into the `summary_content` markdown
  body.

- **Node:** `follow_up_prep_node`
  **Role:** Renumbers citations and builds the follow-up `chat_payload`.

- **Node:** `follow_up_post_node`
  **Role:** Assembles the `final_response` envelope from the renumbered text
  plus the follow-up list.

- **Node:** `chat`
  **Role:** Mounted chat subgraph reused by the plan-query, summary, and
  follow-up sites via the prep/post pairs.

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
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/environment/state.py`](../../src/mcp_server_phytomni/agents/environment/state.py).
`region_vci_analysis` is now a thin wrapper that delegates to the
compiled subgraph via `ainvoke_graph`.

- **TypedDict:** `EnvironmentInput`
  **Required keys:** `query`
  **Optional keys:** `batch`, `kwargs`

- **TypedDict:** `EnvironmentOutput`
  **Required keys:** —
  **Optional keys:** `vci_analysis_task`

- **TypedDict:** `EnvironmentState`
  **Required keys:** `query`
  **Optional keys:** `batch`, `kwargs`, `region_codes`, `vci_analysis_task`

The graph compiles into two nodes wired with one conditional edge:

- **Node:** `extract_region_codes_node`
  **Role:** Issues the chat extraction that parses
  `<result>province|city|county</result>` out of the user query.

- **Node:** `submit_vci_task_node`
  **Role:** Builds the goal prompt + data list, materialises the run-scoped
  output directory, and submits the VCI task to the AnalystAgent.

`route_after_extract` reads `state["region_codes"]`: a populated
list flows to `submit_vci_task_node`, a `None` value short-circuits
to `__end__`. Mirrors the legacy wrapper's early-return on
extraction failure so the wrapper still returns
`{"vci_analysis_task": None}` without invoking the analyst submit.

## Evolution Subgraph

The taxonomy-driven evolution workflow is registered as `evolution`
in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app exposes a narrow IO contract through three
TypedDicts in
[`agents/evolution/state.py`](../../src/mcp_server_phytomni/agents/evolution/state.py).
`evo_test_analysis` is now a thin wrapper that delegates to the
compiled subgraph via `ainvoke_graph`.

- **TypedDict:** `EvolutionInput`
  **Required keys:** `query`, `species_code`, `gene_id`
  **Optional keys:** `batch`, `enable_auto_select`, `kwargs`

- **TypedDict:** `EvolutionOutput`
  **Required keys:** —
  **Optional keys:** `evolution_agents_task`

- **TypedDict:** `EvolutionState`
  **Required keys:** `query`, `species_code`, `gene_id`
  **Optional keys:** `batch`, `enable_auto_select`, `kwargs`, `target_taxids`,
  `evolution_agents_task`

The graph compiles into two nodes wired with one conditional edge:

- **Node:** `resolve_target_taxids_node`
  **Role:** Issues the chat extraction that parses target species names, then
  fans out per-species HTTP lookups to materialise the taxonomy ids.

- **Node:** `submit_evolution_task_node`
  **Role:** Builds the goal prompt + data list, materialises the run-scoped
  output directory, and submits the evolution task to the
  AnalystAgent.

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
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
The compiled app is the project's largest single graph: it
orchestrates a Part 1 brief_gene preamble (the BriefGeneAgent
mounted as a subgraph), a Part 2 parallel analyst fan-out across
the deep analysis types, and a Part 3 report synthesis chain.

The graph compiles into nineteen nodes (plus `__start__` / `__end__`):

- **Node:** `brief_gene_node`
  **Role:** Required launch barrier: runs the BriefGeneAgent preamble subgraph
  and projects its rendered answer into the verbatim `preamble` field
  (only the H1
  title is swapped to deep_genome's); the report consumes it as the
  pre-analysis
  block. A mount failure raises the fixed `RequiredBriefGeneError`
  before task
  preparation or any remote submission. A successful mount may still
  roll up
  per-symbol `literature_degraded` metadata.

- **Node:** `prepare_tasks_node`
  **Role:** Materialises eleven logical branches and twelve concrete work items
  from the gene id + species code, then fans the logical branches out
  via `Send`.
  The two Digital Design jobs remain separate concrete rows under one
  logical
  branch.

- **Node:** `gene_expression_tissues_node`
  **Role:** Send-dispatched worker (`_run_analyst_node`): submits the
  tissue-axis gene-expression analysis via
  `submit_analyst_via_subgraph`, awaits
  completion, and contributes one synthesize-barrier branch.

- **Node:** `gene_expression_cultivars_node`
  **Role:** Same worker for the cultivar-axis gene-expression analysis.

- **Node:** `gene_expression_treatments_node`
  **Role:** Same worker for the treatment-axis gene-expression analysis.

- **Node:** `gene_expression_genotypes_node`
  **Role:** Same worker for the genotype-axis gene-expression analysis.

- **Node:** `single_cell_node`
  **Role:** Same worker for the single-cell analysis.

- **Node:** `promoter_node`
  **Role:** Same worker for the promoter (motif) analysis.

- **Node:** `smep_node`
  **Role:** Same worker for the SMEP analysis.

- **Node:** `smoc_node`
  **Role:** Same worker for the SMOC analysis.

- **Node:** `protein_structure_node`
  **Role:** Same worker for the protein-structure prediction (lights report
  §protein_structure).

- **Node:** `evolution_node`
  **Role:** Mounted standalone evolution subgraph (single source of truth): runs
  the taxid-scoped `evolution_agents_analysis`;
  `finalize_evolution_result` folds
  it into the analyst fan-out.

- **Node:** `design_node`
  **Role:** Mounted standalone DigitalDesignAgents subgraph: runs the generative
  protein/promoter design tasks; `finalize_design_result` lights
  report §8.2 from
  the protein-design task.

- **Node:** `synthesize_node`
  **Role:** Barrier that derives a `WorkflowOutcome` from all twelve concrete
  work-item rows, waits for every item to become terminal, and
  requires at least
  one usable result before synthesis. Optional failures are surfaced
  as degraded
  metadata; all-failed or missing synthesis raises instead of reaching
  the
  experiment loop.

- **Node:** `experiment_node`
  **Role:** Loops over recommended experiments (also routes back to itself per
  Send) to keep building the experiment list.

- **Node:** `protocol_node`
  **Role:** Calls `_dispatch_knowledge_retrieve` per experiment to retrieve
  protocol sections through the compiled knowledge subgraph.

- **Node:** `discussion_node`
  **Role:** Calls `_dispatch_chat` to generate the discussion section from the
  part-1 + part-2 content.

- **Node:** `summary_node`
  **Role:** Calls `_dispatch_chat` to generate the summary section from the
  introduction + part-1 + part-2 + part-4 stack.

- **Node:** `follow_up_node`
  **Role:** Calls `_dispatch_chat` to generate the follow-up question list from
  the assembled final report.

The four `_dispatch_*` helpers on `DeepGenomeReportMixin` own the
chat / knowledge seams: `_dispatch_chat` (used by the experiment /
discussion / summary / follow-up nodes) routes through the compiled
chat subgraph; `_dispatch_knowledge_retrieve` (used by the protocol
node) routes through the compiled knowledge subgraph. Each generic
analysis type fans out to its own named worker node (all reusing
`_run_analyst_node` → `submit_analyst_via_subgraph`); `evolution_node`
and `design_node` mount the standalone evolution / design subgraphs.

**Mount config isolation (intentional, tenant-safe).** The three mounted
subgraphs — `brief_gene_node`, `evolution_node`, `design_node` — are
built with their own module-default config, **not** the parent
`DeepGenomeAgents`' per-request `deep_genome_config` / `sensitive_config`.
Only `knowledge_app` inherits the parent config; `brief_gene_node` reuses
the parent `knowledge_agent`, so its retrieve path inherits while its own
BI / prompt config stays default. This is deliberate, not an oversight:
credentials resolve from the same process `.env` via
`get_sensitive_config()`, so module-default equals the parent's secrets
in any single-operator deployment; and every analyst submission the
evolution / design mounts make is routed by `ensure_analysis_output_dir`
to the content-addressed, tenant-neutral `shared_output_key`, overriding
any user-scoped `output_dir` the mount preset — so sub-task results land
at a key that carries no `user_id` regardless of the mount's config or
(anonymous) user. The parent's `USER_ID` scopes only the ephemeral local
download scratch dir, never a stored object key. The tenant neutrality
this rests on is pinned by
[`test_dispatch_context_user_neutral.py`](../../tests/unit/test_dispatch_context_user_neutral.py)
and `test_shared_output_key_is_tenant_neutral`. Threading the parent's
per-request config into these mounts is therefore **not** a fix to apply
blindly: it would reintroduce a user-scoped `output_dir` into the mounts
and make tenant isolation depend on the dispatch-seam override never
having a gap, where today the mounts simply carry no tenant state to leak.

## Nested Checkpoints

LangGraph's `parent.add_node("name", child_compiled_app)` pattern
shares the parent's `thread_id` through the `configurable` dict to
the embedded child. The spike in
[`tests/agents/test_nested_checkpoint_spike.py`](../../tests/agents/test_nested_checkpoint_spike.py)
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

[`graphs/manifests/`](../../src/mcp_server_phytomni/graphs/manifests/)
contains JSON snapshots produced by
`export_manifest(compiled_app).model_dump_json(indent=2)` for the
analyst / brief_gene / chat / data / deep_genome / environment /
evolution / knowledge / review subgraphs (nine total). Snapshots
act as a visible contract for parent-graph authors and as
regression bait — any node-set or edge-set drift surfaces as a
diff in the same PR that causes it. A CI re-export-and-diff guard
remains pending. See [Declarative Graphs](#declarative-graphs) for
the reverse direction: reading a manifest back into a validated
view via `load_graph_manifest`.

## Declarative Graphs

`graphs/loader.py` ships a default-off `load_graph_manifest`
function that reads any committed
`graphs/manifests/*.graph.json` snapshot back into a
validated `GraphManifest` view. It is the read-back complement of
the `export_manifest()` write path and the foundation for future
LLM-authored or human-edited manifests that compile into LangGraph
apps.

**Feature flag.** `GRAPH_LOADER_ENABLED` on `ServerConfig` (env
`GRAPH_LOADER_ENABLED` or `PHYTOMNI_GRAPH_LOADER`) gates
the call; the default is `False`. With the flag off,
`load_graph_manifest(...)` raises `GraphLoaderDisabledError`, so
importing the function does not enable anything. Set
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
from mcp_server_phytomni.graphs.loader import load_graph_manifest

manifest = load_graph_manifest(
    "src/mcp_server_phytomni/graphs/manifests/chat.graph.json"
)
print(manifest.subgraph_node_names)
```

## Adding a New Subgraph

1. Define `Input` / `Output` / `State` TypedDicts in
   `agents/<domain>/state.py`.
1. Build the graph with
   `StateGraph(state_schema=State, input_schema=Input, output_schema=Output)` so
   parent graphs see a stable, narrow
   contract.
1. Register the compiled app with the project's central
   `SubgraphRegistry` (the registration site grows as Phases land).
1. Parent graphs load it either by
   `parent.add_node("name", subgraph_compiled_app)` (when state keys overlap)
   or by
   `parent.add_node("name", adapter_node(map_in, subgraph, map_out))`
   (when schemas need translation).
