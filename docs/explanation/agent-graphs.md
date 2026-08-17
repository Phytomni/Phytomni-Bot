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

Registered as `chat` in
[`graphs.defaults.build_default_registry()`](../../src/mcp_server_phytomni/graphs/defaults.py).
TypedDicts live in
[`chat/state.py`](../../src/mcp_server_phytomni/agents/chat/state.py):
`ChatInput` requires `user_query`; `ChatOutput` requires `response`.

Stages: prepare uploaded OBS context → generate via `_run_phyto_chat`
(cache keys match the legacy path) → optional follow-up. After
generate, `chat_kwargs["with_follow_up"]` (default `True`) chooses
follow-up or `END`. One compiled graph serves both `phyto_chat` and
`phyto_chat_with_follow`. Node names: `graphs/manifests/chat.graph.json`.

## Knowledge Subgraph

Registered as `knowledge`. TypedDicts in
[`agents/knowledge/state.py`](../../src/mcp_server_phytomni/agents/knowledge/state.py);
`KnowledgeAgentState` is a back-compat alias. Input requires
`user_query`; output requires `retrieved_docs` and `final_response`.

Stages: process files → retrieve+rerank → generate (prep / mounted
`chat` / post) → optional follow-up the same way. Start may skip file
processing; retrieve may skip generate. Manifest:
`graphs/manifests/knowledge.graph.json`.

## Data Subgraph

Registered as `data`. TypedDicts in
[`agents/data/state.py`](../../src/mcp_server_phytomni/agents/data/state.py);
`DataAgentState` is a back-compat alias. Input requires `user_query`;
output requires `final_response`.

When `is_rewrite` is set, a mounted `knowledge` subgraph builds the
rewrite prompt and a mounted `chat` subgraph rewrites the NL question;
otherwise start goes straight to `search_node` /
`nl2sql.execute_nl2sql_request`. Manifest: `graphs/manifests/data.graph.json`.

## Analyst Subgraph

Registered as `analyst`. TypedDicts in
[`agents/analyst/state.py`](../../src/mcp_server_phytomni/agents/analyst/state.py);
`AnalystAgentsState` is a back-compat alias. Input requires `query`
and may carry `compute_resource`, `data_list`, `obs_file_list`, and
plan flags.

Stages: parse query → optional data select → method retrieve
(mounted `knowledge`) → plan → check (may loop) → tool extract →
tool retrieve → submit → optional poll. Five LLM sites share one
mounted `chat` node via prep/post pairs. Manifest:
`graphs/manifests/analyst.graph.json`.

`graphs/analyst_dispatch_adapters.py` ships
`map_send_payload_to_analyst_input` and
`map_analyst_output_to_dispatch_state` for parent graphs (design /
network / research / deep_genome) that compose analyst via
`adapter_node`.

## Research parent

`InSilicoResearchAgent` decomposes a paper or research goal into
computational tasks. It has no standalone `graphs/manifests/*.graph.json`.
Child analysis submissions go through `submit_analyst_via_subgraph` and
the Analyst adapters above. Default compute tier is `medium`; see
[Compute resource tiers](../reference/configuration.md#compute-resource-tiers).

## Design parent

`DigitalDesignAgent` runs protein and promoter design workflows. It has
no standalone graph manifest. Design jobs reuse Analyst through the
same dispatch seam. Baseline compute is `small`;
`protein_design_analysis` and `protein_structure_analysis` map to
`medium`.

## Network parent

`GeneNetworkAgent` analyses a species and trait-ontology id. It has no
standalone graph manifest. Network submissions reuse Analyst through
the same dispatch seam. Default compute tier is `small`.

## BriefGene Subgraph

Registered as `brief_gene`. TypedDicts in
[`agents/brief_gene/state.py`](../../src/mcp_server_phytomni/agents/brief_gene/state.py).
Input requires `user_query`; DeepGenome consumes `final_response`
verbatim (H1 title swapped).

Stages: judge gene id → annotation + homology (static fan-out, not
`Send`) → per-symbol retrieve (`Send` workers; a recovered fault stays
SUCCESS via `literature_degraded`, never `failures`) → four profile
sections → introduction → `render_node`. A non-empty
`literature_degraded` list prepends a banner; the happy path stays
byte-identical for the DeepGenome mount. Follow-up uses mounted `chat`
only when `is_follow_up` (parents set it false). Manifest:
`graphs/manifests/brief_gene.graph.json`.

## Review Subgraph

Registered as `review`. TypedDicts in
[`agents/review/state.py`](../../src/mcp_server_phytomni/agents/review/state.py)
(`DeepResearchState`). Input requires `original_user_query`.

Not a linear pipeline: plan dimensions, then four sequential
dispatch → worker → reduce stages (`retrieve`, `draft`,
`review_results`, `revised`), then summary and citation-renumbering
follow-up. Plan / summary / follow-up share mounted `chat`. To skip
trailing summarisation, mount via `adapter_node` and ignore
`summary_content`. Manifest: `graphs/manifests/review.graph.json`.

## Environment Subgraph

Not an MCP tool. Registered as `environment`. TypedDicts in
[`agents/environment/state.py`](../../src/mcp_server_phytomni/agents/environment/state.py).
Extract region codes, then submit a VCI Analyst job; a missing extract
returns `{"vci_analysis_task": None}`. Manifest:
`graphs/manifests/environment.graph.json`.

## Evolution Subgraph

Not an MCP tool. DeepGenome mounts it. Registered as `evolution`.
TypedDicts in
[`agents/evolution/state.py`](../../src/mcp_server_phytomni/agents/evolution/state.py).
Resolve target taxids (SPA FAQ), then submit an Analyst job. Both
failure and success use `{"evolution_agents_task": ...}`. Manifest:
`graphs/manifests/evolution.graph.json`.

## DeepGenome Subgraph

Registered as `deep_genome`. Largest graph: required BriefGene
preamble, twelve concrete analysis work items, then synthesis.

- BriefGene is the launch barrier (`RequiredBriefGeneError` before any
  remote submit).
- `prepare_tasks_node` materialises eleven logical branches / twelve
  concrete rows (two Digital Design jobs share one logical branch) and
  `Send`-fans them out.
- Generic analyses reuse `_run_analyst_node` →
  `submit_analyst_via_subgraph`. `evolution_node` and `design_node`
  mount the standalone subgraphs.
- `synthesize_node` waits for every row, needs at least one usable
  result, then experiment / protocol / discussion / summary / follow-up
  run through compiled chat and knowledge helpers.

**Mount config isolation (intentional, tenant-safe).**
`brief_gene_node`, `evolution_node`, and `design_node` use
module-default config, not the parent's per-request objects.
`ensure_analysis_output_dir` writes to the tenant-neutral
`shared_output_key`; the parent's `USER_ID` scopes only local scratch.
Do not thread the parent config into these mounts — that would put a
user-scoped `output_dir` back into the mounts. Pinned by
[`test_dispatch_context_user_neutral.py`](../../tests/unit/test_dispatch_context_user_neutral.py)
and `test_shared_output_key_is_tenant_neutral`. Manifest:
`graphs/manifests/deep_genome.graph.json`.

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

`graphs/loader.py` ships `load_graph_manifest`, which reads any
committed `graphs/manifests/*.graph.json` snapshot back into a
validated `GraphManifest` view. It is the read-back complement of
the `export_manifest()` write path and the foundation for future
LLM-authored or human-edited manifests that compile into LangGraph
apps.

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

**Tour.** A smoke load in a Python REPL looks like:

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
