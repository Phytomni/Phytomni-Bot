# MCP Tool Reference

This page is the human-facing reference for the public MCP tool surface.
The source of truth for validation remains
`src/mcp_server_phytomni/mcp/schemas.py`; the examples here mirror the
committed demo payloads under `demo_data/payloads/`.

For tools that accept `obs_file_list`, pass an empty list (`[]`) when no
uploaded document context is available. Async tools return one or more
task ids and should be polled with `GetTaskStatus`.

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

## Tool Inventory

| Tool                    | Kind  | Required arguments                               | Demo payload                                                                         |
| ----------------------- | ----- | ------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `ChatAgent`             | sync  | `user_query`, `obs_file_list`                    | [chat_agent.json](../demo_data/payloads/chat_agent.json)                             |
| `KnowledgeAgent`        | sync  | `user_query`, `obs_file_list`                    | [knowledge_agent.json](../demo_data/payloads/knowledge_agent.json)                   |
| `DataAgent`             | sync  | `user_query`                                     | [data_agent.json](../demo_data/payloads/data_agent.json)                             |
| `ReviewAgent`           | sync  | `user_query`, `obs_file_list`                    | [review_agent.json](../demo_data/payloads/review_agent.json)                         |
| `BriefGeneAgent`        | sync  | `user_query`                                     | [brief_gene_agent.json](../demo_data/payloads/brief_gene_agent.json)                 |
| `AnalystAgent`          | async | `goal_description`, `data_list`, `obs_file_list` | [analyst_agent.json](../demo_data/payloads/analyst_agent.json)                       |
| `DeepGenomeAgent`       | async | `species_code`, `gene_id`                        | [deep_genome_agent.json](../demo_data/payloads/deep_genome_agent.json)               |
| `InSilicoResearchAgent` | async | `user_query`, `data_list`, `obs_file_list`       | [in_silico_research_agent.json](../demo_data/payloads/in_silico_research_agent.json) |
| `DigitalDesignAgent`    | async | `species_code`, `gene_id`, `obs_file_list`       | [digital_design_agent.json](../demo_data/payloads/digital_design_agent.json)         |
| `GeneNetworkAgent`      | async | `species_code`, `to_id`, `obs_file_list`         | [gene_network_agent.json](../demo_data/payloads/gene_network_agent.json)             |
| `GetTaskStatus`         | sync  | `task_id`                                        | [get_task_status.json](../demo_data/payloads/get_task_status.json)                   |

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

### `ReviewAgent`

Use for broad literature review or report generation that needs planning,
retrieval, drafting, critique, revision, and summary. Use
`KnowledgeAgent` for narrower evidence-backed Q&A.

### `BriefGeneAgent`

Use for concise gene or transcript function reports from BI annotations
and retrieved literature. It expects a single gene/transcript identifier
through `user_query` and does not accept uploaded files.

## Async Tools

### `AnalystAgent`

Use for bioinformatics workflow planning and task submission from a
research goal plus input datasets. `data_list` maps complete OBS dataset
paths to descriptions of their role, format, organism, condition, and
intended analysis use.

Identical submissions are deduplicated by fingerprint over
`goal_description`, `data_list`, and `obs_file_list`. In-flight or
succeeded matches reuse the prior `task_id`; failed and cancelled rows do
not block a fresh submission.

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
registry state, output directory, and one live platform status check when
available.

For a succeeded `DeepGenomeAgent` task, `formatted.answer` carries the
assembled report markdown (the workflow runs in the background and
persists its report on the task row); other agents keep the bare
`Task <id>: <status>` status line and surface their products through
`metadata.output_dir` / `metadata.artifacts`.
