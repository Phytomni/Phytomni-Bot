# MCP Tool Reference

This page is the human-facing reference for the public MCP tool surface.
The source of truth for validation remains
`src/mcp_server_phytomni/mcp/schemas.py`; the examples here mirror the
committed demo payloads under `demo_data/payloads/`.

For tools that accept `obs_file_list`, pass an empty list (`[]`) when no
uploaded document context is available. Async tools return one or more
task ids and should be polled with `GetTaskStatus`.

## Uploading documents for `obs_file_list`

Tools that accept `obs_file_list` expect public OBS paths such as
`/obs/<bucket>/...`, not local filesystem paths.

**HTTP upload (recommended):**

1. Start `phytomni-api` with a valid API key and negotiate the `file_upload`
   descriptor from `GET /v1/agents`.
1. `POST /v1/files` with JSON metadata to create an asset.
1. Upload each exact-length part to the returned upload URL with the opaque
   capability and `X-Phytomni-Part-SHA256`, then call the complete route.
1. Put the completed `asset_id` in an HTTP agent request's `attachments` list.
   Bot resolves the asset to its internal `obs_file_list` only after checking
   the authenticated owner.

Full contract, limits, and curl examples:
[HTTP API — resumable file upload](http-api.md) (`/v1/files` routes).

**Demo files:** small markdown / PDF / xlsx / FASTA samples live under
[`demo_data/`](../../demo_data/). JSON tool payloads under
`demo_data/payloads/` often use `"obs_file_list": []`; use the HTTP
`attachments` field when you need document context.

**MCP-only:** there is no MCP upload tool or asset-id attachment field on the
stdio surface. Obtain an already-provisioned OBS path through the operator's
trusted boundary, then call the MCP tool with that path. Pass `[]` when no
document context is needed.

## Response Envelope

Every MCP tool response ships as a JSON envelope with two top-level
blocks:

- `formatted`: normalized display view (`answer` / `follow_up_questions`
  / `metadata` / `references` / `tabular` / `output_dirs`). DataAgent
  uses `tabular = {"headers": [...], "rows": [...]}` plus a human-readable
  summary in `answer`; DigitalDesign uses `output_dirs` for fan-out
  paths and mirrors the primary path in `metadata.output_dir`.
- `raw`: sanitized handler payload preserving provider-returned
  reasoning_content / usage / tool_calls / system_fingerprint /
  finish_reason and any forward-compatible extension keys.
  `raw.phytomni_state` carries the agent's LangGraph intermediate
  state (retrieved_docs, gene_id, rewrite_query, research_dimensions,
  plan, tool_usages, ...) when the agent populated them. Credential-
  pattern keys are stripped recursively at this seam before clients
  see them. The chat boundary also normalizes the narrow provider fault
  where a closed `<think>...</think>` block carries the final answer tail
  in `reasoning_content` or at the front of `content`; after this repair,
  `formatted.answer` and `raw.choices[].message.content` both carry the
  display answer.

The HTTP API and MCP stdio surfaces emit the identical envelope. The
client-side `mcp_client_phytomni.tool_result_formatters.parse_formatted_result`
deserializes the `formatted` block back into `FormattedToolResult`;
clients that want the raw block read `payload["raw"]` directly.

**Default mode**: MCP stdio responses contain only `formatted`; the
`raw` block is omitted to reduce response volume. Set
`PHYTOMNI_DEBUG=1` to include `raw` in every response. The HTTP API
supports a per-request `debug` flag (see
[HTTP API](http-api.md#response-projection)).

## Research/Design outbound interop controls

`InSilicoResearchAgent` and `DigitalDesignAgent` accept two optional
request fields:

```json
{
  "interop_mode": "auto",
  "interop_targets": ["mcp-peer", "a2a-peer"]
}
```

`interop_targets` contains only operator-registered ids; callers cannot
provide a URL, command, credential, or token. The mode is deliberately
closed to three values:

- **Mode:** `off`
  **Behavior:** Local Analyst execution; no external discovery or invocation.

- **Mode:** `auto`
  **Behavior:** Use an eligible MCP/A2A capability when available, otherwise
  continue locally and set
  `formatted.metadata.degraded_interop=true`.

- **Mode:** `required`
  **Behavior:** Require bounded external evidence; discovery, timeout,
  transport, or empty-evidence failures are surfaced instead of
  becoming local
  pseudo-success.

When a peer returns `input-required`, the graph pauses before local Analyst
submission and resumes with the existing run resume mechanism. The default
formatted result may include `metadata.interop`, a list containing only
`target_id`, `kind`, `capability`, `status`, and `latency_ms`. Endpoint URLs,
credentials, protocol correlation ids, and peer payloads remain out of that
projection; inspect the sanitized `raw.phytomni_state` only when debug mode
is explicitly enabled.

## Tool Inventory

- **Tool:** `ChatAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Demo payload:** [chat_agent.json](../../demo_data/payloads/chat_agent.json)

- **Tool:** `KnowledgeAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Demo payload:**
  [knowledge_agent.json](../../demo_data/payloads/knowledge_agent.json)

- **Tool:** `DataAgent`
  **Kind:** sync
  **Required arguments:** `user_query`
  **Demo payload:** [data_agent.json](../../demo_data/payloads/data_agent.json)

- **Tool:** `ReviewAgent`
  **Kind:** sync
  **Required arguments:** `user_query`, `obs_file_list`
  **Demo payload:**
  [review_agent.json](../../demo_data/payloads/review_agent.json)

- **Tool:** `BriefGeneAgent`
  **Kind:** sync
  **Required arguments:** `user_query`
  **Demo payload:**
  [brief_gene_agent.json](../../demo_data/payloads/brief_gene_agent.json)

- **Tool:** `AnalystAgent`
  **Kind:** async
  **Required arguments:** `goal_description`, `data_list`, `obs_file_list`
  **Demo payload:**
  [analyst_agent.json](../../demo_data/payloads/analyst_agent.json)

- **Tool:** `DeepGenomeAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `gene_id`
  **Demo payload:**
  [deep_genome_agent.json](../../demo_data/payloads/deep_genome_agent.json)

- **Tool:** `InSilicoResearchAgent`
  **Kind:** async
  **Required arguments:** `user_query`, `data_list`, `obs_file_list`
  **Demo payload:**
  [in_silico_research_agent.json](../../demo_data/payloads/in_silico_research_agent.json)

- **Tool:** `DigitalDesignAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `gene_id`
  **Demo payload:**
  [digital_design_agent.json](../../demo_data/payloads/digital_design_agent.json)

- **Tool:** `GeneNetworkAgent`
  **Kind:** async
  **Required arguments:** `species_code`, `to_id`
  **Demo payload:**
  [gene_network_agent.json](../../demo_data/payloads/gene_network_agent.json)

- **Tool:** `GetTaskStatus`
  **Kind:** sync
  **Required arguments:** `task_id`
  **Demo payload:**
  [get_task_status.json](../../demo_data/payloads/get_task_status.json)

## Sync Tools

### `ChatAgent`

Use for general plant-science Q&A, conceptual explanation, and uploaded
file summarization. It does not perform literature retrieval, SQL-backed
lookup, or remote workflow submission.

### `KnowledgeAgent`

Use for targeted evidence-backed answers over literature, patents, books,
and optional uploaded documents. Answers may include inline citation
markers and reference metadata in the formatted response.

### `DataAgent`

Use for natural-language questions that should be converted into
SQL-backed botanical database queries. The MCP and HTTP surfaces return
the result inline; it is not a remote async task.

Transient upstream NL2SQL gateway timeouts (for example HTTP 504) are
surfaced as a failed DataAgent call. Phytomni-Bot does not change the
upstream platform SLA; retry the question or check operator status if
failures persist.

Shared HTTP helpers retry transient transport errors quietly; operator
logs record a full traceback only after retries are exhausted. A final
success after retries is normal and not a Bot failure.

### `ReviewAgent`

Use for broad literature review or report generation that needs planning,
retrieval, drafting, critique, revision, and summary. Use
`KnowledgeAgent` for narrower evidence-backed Q&A.

### `BriefGeneAgent`

Use for a rich gene/transcript preamble — an introduction plus a
`## Gene Profiles` block (Basic Genomic Information bullets and four
analytical sections) built from BI annotations and retrieved
literature. It expects a single gene/transcript identifier through
`user_query` and does not accept uploaded files. DeepGenomeAgents mounts
this same preamble verbatim above its `## Bioinformatic Analysis` body.

## Async Tools

The submit response is a submission acknowledgement, not a completed report.
Use `GetTaskStatus` or `GET /v1/runs/{run_id}` for one non-blocking lookup. At
the terminal HTTP run aggregate, a succeeded analyst-class task exposes
`final_report`; Design and Network also expose their real artifact/output
paths. Offline mocks validate the shape; this does not prove live backend
acceptance.

### `AnalystAgent`

Use for bioinformatics workflow planning and task submission from a
research goal plus input datasets. `data_list` maps complete OBS dataset
paths to descriptions of their role, format, organism, condition, and
intended analysis use.

Identical submissions are deduplicated by a content fingerprint over
`goal_description`, `data_list`, and `obs_file_list`. The shared
`runtime/task_dedup.py` helpers cover both this top-level path and the
dispatch seam that the other async analysis tools (`DigitalDesignAgent`,
`GeneNetworkAgent`, `InSilicoResearchAgent`, `DeepGenomeAgent`) funnel
through. A fingerprint hit is verified against the live remote status before
reuse: an in-flight or succeeded match reuses the prior remote task, while a
failed or cancelled task is written back and resubmitted (a polling caller
reuses only a terminal-success task). Results are content-addressed under
`agent_data/shared/<fingerprint>/output/` — no submitter's identity is in the
path — and a reuse always returns the caller's own fresh task id rather than
the prior submitter's.

### `DeepGenomeAgent`

Use for comprehensive gene-function analysis from `species_code` plus one
target `gene_id`. `species_code` is the supported three-letter species
code from the schema, such as `osa` for rice or `zma` for maize.

### `InSilicoResearchAgent`

Use to extract computational research objectives from a paper, uploaded
context, or free-form research goal, then submit reproducibility-style
analysis tasks against the provided datasets.

### `DigitalDesignAgent`

Use to submit protein and promoter design analyses for one supported
three-letter `species_code` plus one `gene_id`. This is a design-task
submission tool, not a general gene-function explainer.

### `GeneNetworkAgent`

Use to submit trait-associated gene network analysis for one supported
three-letter `species_code` plus one Trait Ontology id, formatted like
`TO:0000207`.

## Status Tool

### `GetTaskStatus`

Use to check a previously submitted async task without blocking. Pass the
`task_id` returned by `AnalystAgent`, `DeepGenomeAgent`,
`InSilicoResearchAgent`, `DigitalDesignAgent`, or `GeneNetworkAgent`.

Unrecorded ids return `status: "unknown"`. Known ids return the local
registry state and output directory. Remote analysis-platform child
tasks (and DeepGenome rows that carry a `source_task_id`) also receive
one live platform status probe when available. A `DeepGenomeAgent`
umbrella without `source_task_id` is local-only: reconcile skips the
remote jobs probe and uses the in-process coordinator plus the persisted
local snapshot (including its latest `intermediate_report` and
`final_report`).

For a `DeepGenomeAgent` task, `formatted.answer` chooses the final report,
then the latest intermediate report, then the `Task <id>: <status>` line. The
workflow runs in the background and persists each report revision on the
umbrella row; other agents keep the bare status line and surface their
products through `metadata.output_dir` / `metadata.artifacts`.

The DeepGenome status metadata carries `report_stage`,
`report_completeness`, `report_revision`, `report_updated_at`, ordered
`progress` counts, fixed-message `failures`, `degraded`, and sanitized
`degraded_reason`. Optional analyses may fail independently. BriefGene is
required; a BriefGene failure fails the umbrella without a report. Once
BriefGene succeeds, a failed or still-running optional analysis leaves the
best intermediate report visible, while a final report appears only after
synthesis succeeds.

The local DeepGenome store reserves the owner run, umbrella task, and required
BriefGene section atomically before scheduling the coordinator. It keeps
logical sections in `deep_genome_sections` and normalized child identities in
`deep_genome_remote_tasks`. The coordinator owns bounded polling and writes
monotonic report revisions; `GetTaskStatus` only reads the resulting snapshot.
An API restart does not resume the in-process coordinator, so an orphan is
settled to the fixed restart-failure boundary while its latest intermediate
report remains readable. The one-shot stdio command cannot outlive its server
subprocess; use the HTTP-backed CLI or a persistent MCP session for follow-up.

A degraded `DeepGenomeAgent` report (an optional evolution or digital-design
sub-analysis failed mid-run) keeps surfacing the report but adds
`metadata.degraded` (bool) and
`metadata.degraded_reason` (a redacted string, or `null`); healthy and
non-DeepGenome rows read `false` / `null`.

BriefGene is a required pre-launch branch. If it fails, DeepGenome fails with
the fixed public error `brief gene profile failed`, emits no report, and does
not submit any remote analysis jobs; no degraded success banner is emitted.

A `DeepGenomeAgent` umbrella whose background run died without persisting
a report — a lost best-effort terminal-status write, or a process
restart that orphaned the in-flight task — reconciles read-time to
`failed` rather than showing `running` forever. A still-live run keeps
reading `running`, and a completed run whose only loss was the terminal
write still reads `succeeded` from its persisted report. This liveness
check is process-local, so run the API single-worker (see the runbook's
multi-worker caveat).

## Progress Notifications

MCP stdio clients may subscribe to in-band progress during long
graph-agent runs. Set a `progressToken` in the request meta and the
server sends one MCP `notifications/progress` per graph reduce or
section node, carrying:

- `progress` — the completed-step count (`current` from the
  underlying `ProgressEvent`).
- `total` — the stage denominator when known, else absent.
- `message` — the semantic phase label (e.g. `"retrieving"`,
  `"drafting"`, `"revising"`).

Four tools emit progress on stdio: `KnowledgeAgent`, `ReviewAgent`,
`DataAgent`, and `BriefGeneAgent`. Each emits one tick per graph
reduce/section node during its run.

`ChatAgent` does **not** emit stdio progress: it is not a graph agent,
so the call blocks until the answer is ready. On the SSE path
(`phyto-chat` with `stream: true`) ChatAgent token-streams its answer
directly, which serves as its own liveness signal.

The Python client (`mcp_client_phytomni`) accepts a
`progress_callback` on `call_tool`; the MCP SDK invokes it for each
server progress notification:

```python
async def on_progress(progress: float, total: float | None,
                      message: str | None) -> None:
    print(f"[{message}] {int(progress)}/{int(total) if total else '?'}")

result = await client.call_tool(
    "KnowledgeAgent",
    {"user_query": "...", "obs_file_list": []},
    progress_callback=on_progress,
)
```

The progress event's fields (`kind`, `phase`, `current`, `total`,
`detail`) preserve the context needed by protocol adapters. In
particular, `phase` describes work within a running task; it is not a
task lifecycle state. An A2A adapter must derive `TaskState` separately
and may carry these progress fields in status metadata.
