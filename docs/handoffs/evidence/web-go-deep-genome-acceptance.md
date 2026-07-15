# Web/Go DeepGenome lifecycle acceptance packet

Owner: Phytomni-Web and Go gateway\
Bot contract: `docs/reference/http-api.md` and
`.codex/handoff/2026-07-15-deep-genome-web-go-handoff.md`\
Current state: `External Pending`\
Evidence: `Not returned`

This packet is the live acceptance sequence for `RC-WEB-001` through
`RC-WEB-005`. It does not change the Bot route or execute a backend call from
this repository. The owner must use an authorized environment, redact the
responses, and return one evidence record per acceptance ID.

## Preconditions

- Record the exact Bot commit and Web/Go release under test.
- Use an owner-scoped API key and a synthetic test dialogue.
- Keep request IDs, timestamps, HTTP status, and sanitized response bodies.
- Do not include child analysis IDs, bearer tokens, upstream URLs, SQL, or
  customer rows in the returned artifact.

## Sequence

### 1. Submit (`RC-WEB-001`)

Call `POST /v1/agents/deep_genome/runs` with the documented synthetic gene
payload. Assert:

- the acknowledgement is `202`/`status=running`;
- exactly one owner-scoped umbrella run ID is returned;
- the client stores the umbrella ID and does not poll child IDs;
- a tracking-degraded response is surfaced as an error rather than a fake ID.

Return the redacted acknowledgement and the client storage assertion.

### 2. Revision polling (`RC-WEB-002`)

Poll `GET /v1/runs/{run_id}` at a bounded cadence. Capture two or more
responses in which `report_revision` increases. Assert that an older or equal
revision never replaces visible content with a blank report and that the
latest `intermediate_report` remains visible while optional work runs.

Return the ordered response samples and the renderer assertion.

### 3. Failure and partial matrix (`RC-WEB-003`)

Run four synthetic backend cases and compare the public projection with the
copyable examples:

| Case                              | Required result                                                                              |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| BriefGene failure                 | `failed`; both reports null; `report_stage=waiting_for_brief_gene`; no optional submissions. |
| All optional analyses unavailable | `failed`; BriefGene-derived intermediate report retained; final report null.                 |
| Optional partial success          | `succeeded`; final report present; `degraded=true`; failure count and chapters visible.      |
| Final synthesis failure           | `failed`; last intermediate report retained; final report null.                              |

Return one redacted fixture per case and link each to its `RC-WEB-003` record.

### 4. Artifact projection (`RC-WEB-004`)

Run Analyst, Design, and Network report cases. Assert that `final_report` and
`formatted.answer` render, and that real `/obs/<bucket>/<key>` paths resolve
through the supported download surface. An empty path list must render a safe
warning; it must not cause the client to invent a URL.

Return the integration test result and sanitized artifact-path assertions.

### 5. Timeout and upstream error (`RC-WEB-005`)

Force an authorized upstream timeout before a usable response and assert that
the gateway returns the documented `504` with a safe message and request ID.
If a known Bot run ID exists, the client must retain it for polling; it must
not turn the timeout into a fake final report or generic success.

Return the response status, request ID, and renderer fallback assertion.

## Review and rollback

The Bot maintainer reviews each returned record against the stable public
schema and the redaction rules. If any case fails, keep the corresponding ID
`External Pending`, restore the prior consumer behavior or feature flag, and
attach the failure without overwriting earlier evidence.
