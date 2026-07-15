# External owner packet index

This index tells each external owner which acceptance records to return for
0.1.3. It is intentionally executable as a checklist but it is not evidence
itself. Owners must return a redacted record based on
[`record-template.json`](record-template.json); the Bot maintainer reviews the
record before changing the tracked matrix.

## Web and Go gateway

| ID | Owner action | Expected returned artifact |
| --- | --- | --- |
| `RC-WEB-001` | Submit a DeepGenome request and poll only the umbrella run ID. | Request/response transcript showing the umbrella ID and owner-scoped status. |
| `RC-WEB-002` | Poll until at least two report revisions are observable. | Redacted JSON showing monotonic `report_revision` and `intermediate_report`. |
| `RC-WEB-003` | Exercise BriefGene failure, all-analysis failure, partial success, and final synthesis failure. | Four response fixtures linked to the corresponding handoff example. |
| `RC-WEB-004` | Consume Analyst, Design, and Network report/artifact paths. | Integration test output proving paths resolve without server-local secrets. |
| `RC-WEB-005` | Exercise upstream timeout and gateway error mapping. | Response transcript showing the documented HTTP error and preserved report. |
| `RC-WEB-006` | Pass A2UI confirm/form/choice and AG-UI stream/error frames through the gateway. | Byte-preservation and fallback test report with request IDs. |
| `RC-WEB-007` | Run Expert dark launch, history dual-read, cutover, and rollback smoke. | Deployment/change record plus before/after history counts. |

The gateway owner must not poll child analysis IDs, rewrite A2UI payloads, or
close a row using a Bot-only mock.

## Operations, DBA, and GitHub

| ID | Owner action | Expected returned artifact |
| --- | --- | --- |
| `RC-OPS-001` | Verify required GitHub variables and secrets by name and scope. | Redacted CI configuration/job link with no values printed. |
| `RC-OPS-002` | Rotate the Web API key and reject the old credential after the overlap window. | Key-rotation change record and sanitized old/new acceptance result. |
| `RC-OPS-003` | Run citation migration, task DB backup/restore, and legacy-retirement rollback checks. | Change record with counts, rollback outcome, and owner approval. |
| `RC-DB-001` | Run the authorized read-only role and consecutive borrower session probe. | Redacted probe output containing SQLSTATE and `RESET ALL` checks. |
| `RC-DB-002` | Run the eighteen-query direct-vs-relay comparison. | Corpus result artifact with normalized columns and reviewed deltas. |

DBA/Ops commands must run in an authorized environment and must not paste a
DSN, password, bearer token, customer row, or raw SQL result into the record.

## Live release and remote ref

| ID | Owner action | Expected returned artifact |
| --- | --- | --- |
| `RC-LIVE-001` | Run the agreed live E2E matrix against configured backends. | Redacted test report tied to the exact commit under test. |
| `RC-REL-001` | Run `make push` for `release/0.1.3`, then independently inspect the remote ref. | Push output and remote-ref observation for the reviewed commit. |
| `RC-REL-002` | Review every matrix row and sign the release disposition. | Release-owner approval linked to all accepted records or approved blockers. |

Network failure, missing credentials, or an unavailable backend leaves the
corresponding row `External Pending`; it is not a successful acceptance.

## Submission procedure

1. Copy `record-template.json` once per acceptance ID.
2. Replace synthetic values with sanitized observations and retain the exact
   commit/environment labels.
3. Attach the record to the owner handoff, without editing the matrix status.
4. The Bot maintainer checks the record shape, redaction, and acceptance rule.
5. Only the maintainer links reviewed evidence and changes the matrix state.
