# Handoff disposition matrix

**Date:** 2026-07-15\
**Source inventory:** the fourteen filenames under the local `.codex/handoff/`
directory\
**Status rule:** missing external proof is never `Closed`\
**Evidence rule:** `External Pending` rows must say `Not returned`

This matrix is the durable index of the original handoffs. The filenames are
copied verbatim so a reviewer can compare them with the local source inventory
without treating the old document's status prose as evidence. Local Bot
commit hashes, focused tests, and gate counts are evidence for the Bot side
only. Web, Go, GitHub, DBA, and production observations remain pending until
their owners return sanitized proof.

## Status vocabulary

| Status             | Meaning                                                                                                           |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `Closed`           | Local and external acceptance evidence is complete and attached. No row currently qualifies.                      |
| `Superseded`       | A later approved contract replaced the original requirement. Residual work is tracked in the replacement handoff. |
| `External Pending` | Bot prerequisites have evidence, but a Web, Go, GitHub, DBA, live backend, or production result is missing.       |
| `Blocked`          | A named external dependency prevents safe progress; the blocker and owner must be explicit.                       |

## Matrix

| Handoff                                                 | Status           | Bot code/test/gate/commit evidence                                                                 | External owner                   | Returned evidence                  | Remaining action                                                                                                          |
| ------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------- | -------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `2026-05-23-python-service-consolidation.md`            | External Pending | `935043b`, `b6d349a`; HTTP CLI and owner-scoped run tests; scoped gate 441+                        | Web history and Operations       | Not returned                       | Perform history ETL or dual-read cutover, ninety-day key rotation, and production sign-off.                               |
| `2026-06-09-web-gateway-cutover-bot-asks.md`            | External Pending | `d5516ad`, `45ed674`; Expert route and API docs/tests; scoped gate 401+                            | Web, Go, Operations              | Not returned                       | Add the Web mode column, keep Expert dark, smoke the resolved-slug envelope, then enable the flag.                        |
| `2026-06-13-analyst-class-result-assembly-workorder.md` | External Pending | `83c35bd`, `f72b8ed`; terminal report/artifact tests and Web handoff; scoped gate 601+             | Web and live backend owners      | Not returned                       | Run four-agent terminal acceptance and verify Web consumes reports and artifact paths.                                    |
| `2026-06-26-bot-progress-streaming-handoff.md`          | Superseded       | `f1916ed`, `cd07127`; opened-stream lifecycle and failure-boundary tests; scoped gate 513+         | Web and Go for residual fallback | N/A (superseded)                   | Use the narrowed 2026-06-28 AG-UI contract; retain reserved Data/BriefGene fallback work in the new ledger.               |
| `2026-06-28-agui-streaming-contract.md`                 | External Pending | `cd07127`, `6ee9d3d`; lifecycle, cancellation, redaction, and docs gates; scoped gate 512+         | Web and Go gateway               | Not returned                       | Coordinate stream flags, gateway fallback, and live one-error/answer-equivalence evidence.                                |
| `2026-06-29-expert-route-endpoint-workorder.md`         | External Pending | `d5516ad`, `1b5c516`, `45ed674`; route, selector, fallback, and docs tests                         | Web, Go, Operations              | Not returned                       | Apply the additive history migration, then perform dark-to-live Expert cutover and rollback smoke.                        |
| `2026-06-30-citation-table-ops-migration-handoff.md`    | External Pending | `90f1d53`, `4482384`, `da7f52e`; citation enrichment, SQL policy, and staged Ops handoff           | DBA, Web data owner, Operations  | Not returned                       | Import and validate the staging table, run direct/relay smokes, and atomically rename with rollback retained.             |
| `2026-07-08-a2ui-contract-handoff.md`                   | External Pending | `ed5446d`, `f72b8ed`; A2UI multi-turn/fixture tests and Web handoff                                | Go gateway and Web               | Not returned                       | Implement byte-preserving passthrough and return live confirm/form/choice/error evidence.                                 |
| `2026-07-08-gaussdb-unlisten-p0.md`                     | External Pending | `1f8f628`, `078c05f`, `ee47aa0`; reset, read-only, probe, and policy tests                         | DBA and Operations               | Not returned                       | Create the read-only role and run the authorized SQLSTATE and consecutive-borrower probe.                                 |
| `2026-07-09-early-issues-p0.md`                         | Superseded       | `1705a85`, `042d768`, `f72b8ed`; HTTP long-lived report/client path and acceptance handoff         | Web, Go, Operations              | N/A (stdio requirement superseded) | Use HTTP submit/status/follow; close remaining live terminal and external integration items through the current handoffs. |
| `decouple-bot-handoff.md`                               | External Pending | `9060791`, `452d860`, `27b3044`; direct-Gauss cutover, corpus, comparison runner, scoped gate 598+ | Operations and DBA               | Not returned                       | Supply the owner baseline or archived golden and attach the eighteen-query result.                                        |
| `decouple-ops-handoff.md`                               | External Pending | `789e8f6`, `da7f52e`; Gauss controls and guarded Operations handoff; scoped gate 602+              | Operations                       | Not returned                       | Retire legacy services/configuration only after Web/Bot release and rollback evidence.                                    |
| `decouple-overview.md`                                  | External Pending | `f72b8ed`, `da7f52e`; tracked Web/Ops packages and evidence rules                                  | Bot, Web, Go, Operations         | Not returned                       | Maintain the joint ledger until every external row returns an owner transcript.                                           |
| `decouple-web-handoff.md`                               | External Pending | `f72b8ed`, `da7f52e`; Web/Go contract and retirement procedures; scoped gate 602+                  | Web and Go gateway               | Not returned                       | Remove obsolete Web routes, preserve history fallback, run Go/Web gates, and attach deployment evidence.                  |

## Reading the matrix

The two `Superseded` rows are intentionally not green completion claims. The
rich event list was narrowed to the approved AG-UI lifecycle, and the
one-shot stdio survival requirement was replaced by the long-lived HTTP
service plus HTTP-backed CLI. Their residual fallback and live integration
work is represented by the `External Pending` rows above and by the current
[Web/Go handoff](2026-07-15-deep-genome-web-go-handoff.md) and
[Operations handoff](2026-07-15-bot-operations-acceptance-handoff.md).

No row is `Closed` because no returned Web/Go, GitHub, DBA, live backend, or
production transcript is present in this repository. A future owner update
must change the evidence cell and remaining action together; changing only a
status label is not an acceptance record.
