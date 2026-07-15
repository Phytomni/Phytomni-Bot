# Web and Go integration handoff: DeepGenome reports

**Date:** 2026-07-16\
**Bot owner:** Phytomni-Bot maintainer\
**External owners:** Phytomni-Web, Go gateway, and release operations\
**Overall status:** External Pending\
**Evidence:** Not returned

This document is the copyable boundary for the Web and Go teams. It records
what the Bot already exposes, what the consumers must render or forward, and
which live acceptance observations are still missing. The Bot team does not
edit Web/Go source, Web databases, gateway configuration, or production
systems as part of this handoff.

## Contract and evidence rules

The Bot route and snapshot projection are defined by
[HTTP API](../reference/http-api.md) and exercised by
[`test_api_runs_status.py`](../../tests/server/test_api_runs_status.py) and
[`test_handoff_docs.py`](../../tests/unit/test_handoff_docs.py). The examples
in [`examples/`](examples/) are sanitized public projections; they contain no
API key, endpoint credential, customer result row, or remote child identity.

Every external acceptance item below has four fields:

| Field    | Required value before closure                                              |
| -------- | -------------------------------------------------------------------------- |
| Owner    | The Web, Go, or release owner who ran the check                            |
| Status   | `External Pending` until the check is observed end to end                  |
| Evidence | `Evidence: Not returned`, then a sanitized transcript or fixture reference |
| Rollback | The safe consumer-side fallback if the check fails                         |

Local `pytest`, static checks, and the Bot `make scoped` gate are not a
substitute for the returned external evidence.

The live lifecycle sequence is in the
[DeepGenome acceptance packet](evidence/web-go-deep-genome-acceptance.md).
It covers `RC-WEB-001` through `RC-WEB-005`; the packet is prepared locally
but remains `External Pending` until Web/Go returns redacted records.

## DeepGenome submit and polling

DeepGenome is an asynchronous submission. The submit response is an
acknowledgement and is not a report. The coordinator writes one owner-scoped
umbrella id to the Bot SQLite registry and updates the snapshot after
BriefGene and after each optional analysis transition. Web and Go poll the
Bot run id; they do not call the upstream analysis platform and must not poll
the concrete child ids.

### Submit

Use a placeholder environment variable for the key. Never paste a plaintext
`ptm_` value into a ticket, test fixture, shell history, or evidence bundle.

```bash
curl -fsS -X POST "$PHYTOMNI_BASE/v1/agents/deep_genome/runs" \
  -H "Authorization: Bearer $PHYTOMNI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "arguments": {
      "species_code": "osa",
      "gene_id": "Os01g0100100"
    },
    "dialogue_id": "web-demo-dialogue-001"
  }'
```

Expected acknowledgement shape (the id values are illustrative):

```json
{
  "id": "run-demo-deep-genome-001",
  "object": "agent.run",
  "agent": "deep_genome",
  "status": "running",
  "task_ids": ["dg-demo-umbrella-001"],
  "result": {"formatted": {"answer": null}}
}
```

The submit body may carry `degraded_tracking: true` when the remote work was
accepted but the local registry write failed. In that case `id` can be null
and `task_ids` is empty; the client must surface a tracking error rather than
invent a run id. A normal response has one umbrella id in `task_ids`.

### Status lookup

```bash
curl -fsS "$PHYTOMNI_BASE/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $PHYTOMNI_API_KEY"
```

The lookup is owner-scoped and non-blocking. A consumer should poll at a
bounded cadence (for example, two seconds with a product-level deadline),
stop on `succeeded` or `failed`, and retain the last response for rendering.
The Bot coordinator, not the Web or Go request handler, performs remote
analysis polling. A `404` means the authenticated owner cannot see that run;
do not retry it with a child task id or another user's key.

The public `result` contains only these stable report fields:

| Field                          | Meaning                                                                                 |
| ------------------------------ | --------------------------------------------------------------------------------------- |
| `intermediate_report`          | Best assembled Markdown while optional work is pending, or after a post-profile failure |
| `final_report`                 | Published Markdown after synthesis succeeds; null until then                            |
| `report_stage`                 | `waiting_for_brief_gene`, `intermediate`, or `final`                                    |
| `report_completeness`          | `none`, `partial`, or `complete`                                                        |
| `report_revision`              | Monotonic integer; replace visible content only when it increases                       |
| `report_updated_at`            | Canonical UTC timestamp or null                                                         |
| `progress`                     | Ordered planning, BriefGene, and optional-analysis counters                             |
| `degraded` / `degraded_reason` | Safe partial-result signal and fixed public reason                                      |
| `failures`                     | Work-item key, terminal status, and fixed public message; no traceback                  |

Four copyable response states are locked by tests:

- [Running with an intermediate report](examples/deep-genome-running.json)
- [Succeeded with a degraded final report](examples/deep-genome-partial-final.json)
- [Failed after BriefGene with an intermediate report](examples/deep-genome-failed-with-intermediate.json)
- [BriefGene failure with no report](examples/deep-genome-brief-gene-failed.json)

## Revision-aware rendering

Implement rendering as a monotonic projection, not as a one-time submit
response:

1. Keep the last accepted `report_revision` per run.
1. Ignore an older or equal revision; do not replace a visible report with a
   blank field from a stale poll.
1. While `report_stage` is `intermediate`, render `intermediate_report` and a
   non-terminal progress indicator. The report may grow after every poll.
1. When `report_stage` is `final`, render `final_report` and stop replacing it
   with later intermediate content. `final_report` wins even if an older
   formatted answer is still present.
1. Show `degraded` and `failures` as a warning section, not as a fabricated
   successful chapter. Keep the exact `work_item_key` for support telemetry.

The top-level `answer` shortcut is a convenience for history rows: it points
to the latest nonblank report. It is not a second report source and should not
override the revision rule.

## Failed run with intermediate report

When BriefGene succeeded but synthesis or optional work later fails, the run
is terminal `failed`, `final_report` remains null, and the last usable
`intermediate_report` remains visible. Render the report with a persistent
failure banner such as “DeepGenome stopped before final synthesis.” Include
the safe `degraded_reason` and fixed failure messages; do not label the
intermediate text as a complete final report and do not discard it.

The required acceptance assertion is represented by
[the failed-intermediate example](examples/deep-genome-failed-with-intermediate.json).

## BriefGene failure without report

BriefGene is the required profile. If it fails, the umbrella is terminal
`failed`, `progress.brief_gene_status` is `failed`,
`report_stage` is `waiting_for_brief_gene`, `report_completeness` is `none`,
and both report fields are null. No optional analysis is submitted. Render a
profile failure state with retry/support guidance, not an empty Markdown
placeholder and not a degraded success.

The required acceptance assertion is represented by
[the BriefGene-failure example](examples/deep-genome-brief-gene-failed.json).

## Analyst-class terminal reports

The same terminal consumer contract applies to `analyst`, `research`,
`design`, and `network` runs:

- `result.final_report` is the primary Markdown report.
- `result.formatted.answer` is compact display text for a history row or
  fallback card.
- `result.artifacts[].paths` contains concrete `/obs/<bucket>/<key>` paths
  when run-level artifact assembly is requested. Design and Network should
  render these paths in the gallery/download surface; an empty path list is
  an accepted degraded terminal shape, not permission to invent a URL.
- `result.degraded` and task-level failure metadata explain report assembly
  degradation without turning an otherwise successful analysis into a false
  failure.

The default owner-scoped DeepGenome response intentionally strips raw child
rows and private artifacts. Do not depend on `debug=true` for a production
UI. If a support investigation uses debug mode, treat the response as
restricted diagnostic material and redact it before attaching evidence.

## Progress fallback

Real DeepGenome counters are available in `result.progress`. For synchronous
Chat/Knowledge/Review consumers that have no run id before completion, keep
the existing Web pseudo-progress indicator. If a Bot version or agent does
not provide a structured progress field, the UI must fall back to that
indicator and still render the final response. A missing progress field is
not permission to show a fabricated stage or to fail the request.

For Expert mode, the Bot route is `POST /v1/query/route`; the resolved
`agent` slug is returned, never the literal `expert`. The route can select
`research`, `design`, and `network`; those slugs may render as plain text
until the Web map is extended. Invalid structured arguments return `400`,
and an out-of-set route selection returns `502`.

## Gateway timeout mapping

The Go gateway must preserve the distinction between a submission
acknowledgement and an upstream timeout:

| Condition                                            | Gateway response                            | Client action                                                                                   |
| ---------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Bot accepts a remote run                             | `202` with `status=running`                 | Store `id`; poll `GET /v1/runs/{id}`                                                            |
| Bot/upstream call times out before a usable response | `504`                                       | Show a safe timeout message; retry only with the known run id or a new idempotent submit policy |
| Bot returns a user/input/schema error                | Preserve Bot `4xx` and safe `error.message` | Show actionable validation or retry guidance                                                    |
| Bot returns an internal failure                      | Preserve `5xx` class and request id         | Show a generic safe error; retain correlation id                                                |

The gateway must not turn a `504` into `200`, a fake final report, or a
generic `500`. It must not expose upstream URLs, SQL, credentials, stack
traces, or raw provider text. The exact returned status, sanitized message,
Bot run id (if known), gateway request id, and timestamp are required
acceptance evidence.

## A2UI passthrough

Bot is the contract of record at
`POST /v1/runs/{run_id}/a2ui-actions`. The Go gateway owns authentication
and owner checks, then forwards the JSON bytes unchanged and returns the Bot
HTTP status and body unchanged. The provisional Web path may be
`POST /api/v1/conversations/:id/a2ui-actions`, but it must map to the Bot run
id and must not reinterpret widget props.

For a confirm action, the forwarded body is shaped like:

```json
{
  "run_id": "run-contract-1",
  "surface_id": "sfc-contract-1",
  "widget": "confirm",
  "action_id": "accept",
  "payload": {"accepted": true}
}
```

Form and choice cancellation uses `{"cancelled": true}`. Confirm rejection
uses `{"accepted": false}`. Use the copyable fixtures in
[A2UI contracts](../contracts/a2ui/README.md) for downlink, uplink, success,
cancel, error, and two-round surface assertions. A second uplink after
success must remain a Bot `409`, not a gateway success. Every live
passthrough and error-matrix result is **Owner: Go gateway; Status: External
Pending; Evidence: Not returned**.

## Expert route migration

The Bot route is already available and remains dark until the Web database
and feature flag are coordinated. The Web/Go owner must execute the following
in an additive migration window:

1. Back up the Web database and run
   `ALTER TABLE question_agent_logs ADD COLUMN mode VARCHAR(20) NOT NULL DEFAULT 'instant';`.
1. Verify that existing rows read as `instant`, the migration is idempotent
   in the deployment tool, and the Web history tests pass.
1. Deploy the gateway with `bot.expert_enabled: false`; verify that no
   production request is routed to Expert while dark.
1. Exercise `POST /v1/query/route` in a non-production environment and
   confirm the resolved slug plus `result.formatted` shape. For remote slugs,
   verify `202` and normal polling.
1. Flip `bot.expert_enabled: true` only after the migration and smoke test
   evidence are attached. Keep an immediate flag-off rollback.

The migration and flag are one coordinated cutover. A schema change without
the flag, or a flag without the schema, is not an accepted rollout.

**Owner: Web/Go; Status: External Pending; Evidence: Not returned.**

## History ETL and retirement of old Python path

The existing `question_agent_logs` history table and `SyncBotRuns` cron are
the compatibility bridge until the new Bot run projection is proven. Do not
drop or rewrite history during the first cutover.

If the Web team chooses ETL, use an idempotent mapping from the old row to
`GET /v1/runs?dialogue_id=...` fields (`dialogue_id`, `query`, `answer`,
`tool_name`, `status`, and timestamps). Dry-run the row count, null/duplicate
checks, and per-dialogue sample before writing. Run a dual-read period in
which a history view can fall back to the old row when the Bot run is not
yet available. Record the ETL release, source snapshot, destination count,
and rollback point; never attach customer rows to evidence.

Retire the old Python request path only after Go traffic and history reads
show zero use for the agreed observation window. The owner must remove the
route/import/env references in the Web release, run its full Go/Web gate, and
keep a prior release artifact for rollback. If the cutover fails, restore the
prior Web release and flag before removing the old path.

**Owner: Web/Go and release operations; Status: External Pending; Evidence:
Not returned.**

## Live Bot-Go-Web evidence

The following matrix is the required joint acceptance record. Each row needs
a sanitized response or screenshot reference, release/commit identifiers,
UTC timestamp, and the owner who ran it. Until returned, every row remains
pending.

| Check                    | Owner     | Expected observation                                                        | Status           | Evidence               |
| ------------------------ | --------- | --------------------------------------------------------------------------- | ---------------- | ---------------------- |
| DeepGenome submit        | Go        | `202`, `agent=deep_genome`, one umbrella id, no secret                      | External Pending | Evidence: Not returned |
| Intermediate poll        | Web + Go  | Revision increases; visible report is retained while work runs              | External Pending | Evidence: Not returned |
| Partial final            | Web       | `succeeded`, `report_stage=final`, `degraded=true` and failure banner       | External Pending | Evidence: Not returned |
| Post-profile failure     | Web       | `failed` with intermediate report and no final report                       | External Pending | Evidence: Not returned |
| BriefGene failure        | Web       | `failed`, both reports null, no optional-job UI                             | External Pending | Evidence: Not returned |
| Analyst report           | Web       | `final_report` and compact `formatted.answer` render                        | External Pending | Evidence: Not returned |
| Design/Network artifacts | Web       | Real `/obs/...` paths render or an empty-path warning is shown              | External Pending | Evidence: Not returned |
| Timeout                  | Go        | Bot/upstream timeout maps to `504` with safe message and request id         | External Pending | Evidence: Not returned |
| A2UI confirm/form/choice | Go + Web  | Fixture bytes, status, and body survive passthrough; stale action is `409`  | External Pending | Evidence: Not returned |
| Expert dark/cutover      | Web + Ops | Migration is applied before flag; resolved slug and formatted block survive | External Pending | Evidence: Not returned |
| History                  | Web       | ETL/dual-read counts and rollback point are recorded; old path still safe   | External Pending | Evidence: Not returned |

Attach returned evidence to the disposition matrix when it is created. Until
then, this handoff is delivered but not externally accepted.

## Local evidence already available

The Bot repository can prove only the following local claims:

- the public examples parse against the stable projection;
- owner-scoped status tests cover running, intermediate, failed, and report
  revision behavior;
- A2UI fixtures and route shape tests are present;
- offline gates do not call Web, Go, a live database, or a production
  deployment.

Those claims do not change the external status above. **Status: External
Pending. Evidence: Not returned.**
