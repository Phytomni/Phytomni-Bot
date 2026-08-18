# Research input-resolution contract fixtures

This directory is the Bot-owned, tracked contract packet for
`research_input_resolution_v1` version `1`. The JSON files are sanitized
copyable goldens for Web/Go consumers. They are not `Accepted` evidence;
see [Bot Ready versus Accepted](../../ops/bot-ready-versus-accepted.md).
Generate them with:

```bash
uv run --no-sync python scripts/generate_research_input_contracts.py \
  --output docs/contracts/research-input-resolution
```

The generator imports the effective `ApiLimitsConfig`, Research capability
descriptor, and scientific suffix registry. It emits UTF-8 JSON with sorted
keys, two-space indentation, and exactly one trailing newline. `manifest.json`
pins every JSON fixture except itself by relative path and SHA-256 and records
the same trailing-newline rule without recursively hashing the manifest.

## Public request and response

The native Research request is submitted through the existing agent-run
surface (`POST /v1/agents/research/runs`) with an `arguments` object and, when
managed uploads are used, an `attachments` array containing opaque
`asset_id` values. The same Research preparation is reached from the
Expert route (`POST /v1/query/route`) and the five supported native/Expert
HTTP forms. A request may also carry the private conversation envelope when
that separately enabled context contract is in use.

The request must have an `Idempotency-Key` for a non-conversation turn. A
conversation envelope supplies the authoritative identity when the header is
absent. The accepted request fixture contains only a synthetic query,
synthetic opaque asset ids, and a synthetic idempotency value; it is not a
credential or an authorization grant.

Accepted native submission returns the existing `agent.run` envelope. A
fresh remote Research run is accepted with HTTP `202`, a sanitized `run_id`,
and `status: "running"`; terminal or replay responses use the existing
`200` projection. `GET /v1/runs/{run_id}`, list, and refresh project the same
public lifecycle fields. Public rows contain status, stage, counts, safe
ordinals/basenames, stable errors, and bounded timing only. They do not
contain the original query, effective query, provider request, grant, exact
object key, bucket, owner, or child request body.

## Idempotency and query grammars

The identity is the owner plus either the validated header key or the
conversation tuple. A first request reserves one parent run before worker
launch. Repeating the same identity and fingerprint replays the durable
reservation without resolving input or submitting a child again. Reusing an
identity with a different query, attachment identity, locale, or execution
fingerprint returns `research_idempotency_conflict` (`409`) and leaves the
existing run unchanged. A missing or malformed non-conversation identity is
`research_idempotency_key_required` (`400`) and creates no run.

The pure parser recognizes exactly three data forms:

1. an ASCII-case-insensitive trailing `data:` label followed by one strict
   JSON object mapping strings to strings;
1. a fenced strict JSON object immediately associated with `data:`; and
1. one complete configured-bucket object reference per logical line, with an
   optional ASCII Tab and description hint.

JSON rejects comments, trailing commas, duplicate keys, non-string keys or
values, and trailing structured content. Empty JSON descriptions mean no
hint. Commas, spaces, colons, full-width punctuation, and pipes are not
standalone delimiters. The parser preserves the original Unicode query and
exact source spans; only recognized spans are removed from `effective_query`.
Malformed explicit `data:` syntax fails rather than becoming prose.

## Exact-key trust and limits

Managed assets resolve only through the authenticated owner and completed
server-side asset records. The server retains request order and uses the
persisted purpose, safe filename, size, media hint, and snapshot. Pasted
dataset references use a configured-bucket, exact-key metadata/use port. The
port performs metadata `HEAD`/snapshot checks only: it has no list, body-read,
write, delete, signed-URL, or credential operation. It rejects other
schemes, traversal, controls, placeholders, missing keys, unsupported suffixes,
duplicates, and bucket/path escapes. The model receives opaque dataset ids,
not storage coordinates.

The effective defaults and hard ceilings are generated in `catalog.json`:

- current user query: `131072` Unicode code points by default, `1048576`
  hard maximum;
- managed document plus dataset references: `64` by default, `256` hard;
- pasted dataset references: `64` by default, `256` hard; and
- all managed plus pasted references: `128` by default, `256` hard.

Document conversion remains independently bounded at 25 MiB per document and
50 MiB total converted documents. These limits do not become dataset byte
limits, and raising reference counts does not raise upload concurrency,
storage quota, child fan-out, or archive expansion. The scientific registry
is the single source for longest compound suffix matching and catalog formats;
archives are classified but never extracted by input resolution.

## Lifecycle and failures

The only public nonterminal stages are `input_resolution`, `planning`,
`execution`, and `report_assembly`. A stage is a progress label, not a
success claim. A fresh run reserves `input_resolution`, enters `planning`
only after complete validation, enters `execution` only after a child is
accepted, and enters `report_assembly` only after every required child result
is durable. Terminal `succeeded`, `failed`, and `cancelled` clear the stage.

Failed runs retain the compatibility scalar `error: "run failed"` and add a
bounded `failure` object containing code, safe message, stage, retryability,
and HTTP status hint. The fourteen stable error fixtures cover missing or
conflicting idempotency, syntax/path/format/duplicate/not-found failures,
limits, extraction, resolution, protocol readiness, child tracking, and
cancellation conflict. Debug mode does not relax redaction.

`POST /v1/runs/{run_id}/cancel` is owner-scoped. Cancellation wins through a
durable compare-and-set before in-process task cancellation and grant revoke;
late callbacks cannot reopen a terminal row. Cancelling after a child is
accepted or sent detaches that child and terminates last-claim EI jobs.
Revoke failures create private bounded recovery work and never reopen the
parent run.

## Direct and relay operation

Direct readiness requires valid cross-limits and construction of the injected
configured-OBS metadata port. Relay-child readiness is fail-closed until an
authenticated, non-stale operator snapshot advertises
`research_object_grant_v1` version `1`, the `relay:research-input` or
`relay:*` scope, and maxima at least as large as this child. The relay
capability fixture is metadata-only. `relay:obs` is unchanged and does not
authorize this lane; MCP schemas and stdio behavior are unchanged.

The operator grant flow is resolve → verify → use → revoke. Resolve accepts
ordered opaque dataset ids and exact references bound to the parent run and
execution fingerprint. Verify rechecks the snapshot and binding. Use
requires exactly one valid grant per pasted reference, strips the sidecar, and
forwards the established Analyst payload. Revoke is idempotent and owner/
fingerprint scoped; foreign, expired, revoked, or changed-snapshot grants
fail closed. No grant route lists objects or returns dataset bodies.

The relay capability cache has a five-minute TTL, a thirty-second failure
refresh cooldown, single-flight refresh, and a ten-second handshake timeout.
The Research work-unit lease is 60 seconds with a 20-second heartbeat and a
bounded recovery scan batch of 32. The dispatch outbox uses a 60-second
lease and mark-before-send; a call crossing `sent` is reconciled or kept
ambiguous rather than blindly retried. Operator grants live for 180 minutes,
rotate inside the final 15 minutes when the unchanged snapshot permits it,
and remain purge-eligible after a 24-hour grace. These timings are runtime
constants, not client controls.

Private resolution, work, outbox, grant, and idempotency state remains under
the parent run retention policy and is explicitly purged in dependency-safe
order at terminal cleanup. Existing run retention defaults are 24 hours for
succeeded rows and 7 days for failed rows. Purge never deletes the user's
uploaded dataset. Expired or revoked grants are the security backstop even
when revoke recovery is delayed.

## Rollout and evidence boundary

There is no `RESEARCH_INPUT_RESOLUTION_ENABLED` feature flag. Mixed versions
fail closed through protocol/capability compatibility. Intended rollout is
Operations/Web persistence and ingress verification, Bot deployment, Web
capability consumption, then development/staging exercises of all three
grammars before production consideration. Production rollout has not been
performed by this packet, and these fixtures do not prove Web, Go, paired
runtime, staging, or production acceptance.

Local acceptance may use development SQLite paths, an existing OBS explicitly
proven non-production, a real configured model, and synthetic objects. If
endpoint ownership cannot be proven non-production.

Record `Needs Verification` and make no network call. Runtime evidence may
retain only
counts, digests, durations, stable codes, sanitized run/task ids, and stage
timelines. Never commit local databases, `.env` files, live responses,
screenshots, credentials, endpoints, buckets, object names, queries, prompts,
paper text, or provider output.

Rollback stops new version-1 submissions, drains safe pending work, reverts
Bot while retaining additive SQLite tables, and lets grants expire/purge. It
does not drop state, delete uploads, narrow Web columns, or claim cancellation
of an ambiguous remote child.
