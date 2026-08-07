# Agent attachment contract fixtures

Copyable golden JSON for the Bot native mixed document-and-dataset attachment
submission shape. Web and Go consumers can vendor the files and verify the
exact fixture bytes through [`manifest.json`](manifest.json) without live
storage credentials or model-provider access.

## Bot of record

The Bot owns this native route and request schema:

- Route: `POST /v1/agents/analyst/runs`
- Schema: `AgentRunRequest`
- Fixture body: `native_mixed_request.json`

The paired Research path uses the same attachment fields with Research's
native argument keys. Instant Chat remains document-only and rejects a
dataset asset for the selected Chat agent.

## Upload-create purpose values

`POST /v1/files` accepts these purpose values:

- `dataset` -> resolver partition `dataset` (CSV-only at invocation)
- `document` -> resolver partition `document` (preferred document purpose)
- `chat_attachment` -> resolver partition `document` (legacy mapping)

Purpose is chosen at upload-create time, persisted on the registry row, and
treated as immutable for the asset lifecycle. Submission bodies intentionally
omit purpose; the owner-scoped resolver recovers it from the completed row.

## Delegated owner assertion

When `owner_subject` is present, the caller must hold the explicit
`files:delegate` scope. Without that scope the Bot returns `403`. Omitted
`owner_subject` keeps the authenticated principal as the attachment owner.
Owner mismatch or a missing completed asset collapses to a generic `404`.

## Fixture asset-to-purpose table

- Asset `file_11111111111111111111111111111111`
  - purpose `dataset`
  - filename `synthetic-counts.csv`
  - MIME `text/csv`
- Asset `file_22222222222222222222222222222222`
  - purpose `document`
  - filename `synthetic-protocol.pdf`
  - MIME `application/pdf`

Owner assertion in the fixture: `fixture-delegated-owner`.

## Native projection semantics

For Analyst and Research, the Bot projects resolved attachments into the
legacy argument channels before handler dispatch:

- each dataset asset becomes one `data_list` entry keyed by an internal
  managed reference;
- each document asset appends one managed reference to `obs_file_list`;
- request order is preserved inside each purpose partition;
- purpose is never re-supplied at submission time.

Managed dataset entries use exact empty-string values. Arbitrary non-managed
path maps still require nonblank descriptions.

## Redaction guarantees

Public responses, conversation-context staging, and default debug projections
must not echo:

- internal managed references;
- raw `attachments`, `data_list`, `obs_file_list`, or `owner_subject` private
  request fields;
- provider storage coordinates or short-lived upload secrets.

Stable public failure codes for this contract include `403`, `404`, `409`,
and `422` with sanitized bodies.

## Acceptance boundary

These files prove JSON shape, digest stability, owner-scoped purpose
resolution, and Analyst projection only. They are not real object-storage,
Web UI, model-provider, remote-analysis-platform, staging, or production
acceptance. Feature flags and environment activation remain separate owner
evidence outside this packet.

## File index

- `manifest.json`: protocol, endpoint, agent, and SHA-256 digests.
- `native_mixed_request.json`: delegated mixed Analyst request golden.
