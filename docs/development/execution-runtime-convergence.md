# Execution Runtime Convergence Inventory

This document is the migration authority for OpenSpec change
`unified-agent-execution-runtime`. New executions have one public identity
(`execution_id`), one runtime reservation, one Driver, one V2 journal, one Bot
supervisor, and one Web projector. Compatibility code may present or read the
same execution, but may not select another Agent path or write lifecycle state.

## Zero-duplicate rule

The repository has no grandfathered baseline or allowlist. The following are
release-blocking violations, including when they occur in files that predate
this change:

- a normal Web request can choose request-owned Bot execution instead of V2
  admission/outbox dispatch;
- a GET/lifecycle/event endpoint polls a provider, advances an execution, or
  writes an execution/message projection;
- a public Agent is invoked outside `ExecutionRuntime`;
- durable public work is launched with an unregistered `create_task`;
- routing, validation, scientific branching, provider selection, prompts, or
  result shaping is copied into Runtime/Driver/transport code;
- V1 and V2 command transports can both handle the same V2 execution;
- Agent business code emits repetitive lifecycle boilerplate instead of using
  shared graph/tool/provider/checkpoint/artifact instrumentation.

The executable gates are:

- Web: `python scripts/check_execution_runtime_convergence.py`
- Bot: `python scripts/ai_development.py convergence --check`
- catalog/runtime: `tests/unit/test_public_agent_catalog.py` and
  `tests/unit/test_all_agent_runtime_acceptance_v2.py`

Both scanners must report empty responsibility arrays and `violations: []`.
The Bot scanner also rejects secondary runtime writers in transport adapters
and prevents retired A2UI/background lifecycle modules from being restored.

## Canonical public-Agent matrix

All rows use `dispatch_adapter=execution_runtime` and
`event_contract=execution_journal_v2`. The handler named below remains the one
business implementation; Drivers wrap it and do not copy its decisions.

| Agent/tool | Canonical handler | Driver | Topology / join | Resume | Primary side-effect owner |
|---|---|---|---|---|---|
| Chat / `ChatAgent` | `handle_chat_agent` | `resumable_graph` | conditional / n/a | action + recovery | checkpoint Driver + Bot supervisor |
| Knowledge / `KnowledgeAgent` | `handle_knowledge_agent` | `local_graph` | conditional / n/a | none | instrumented local graph Driver |
| Data / `DataAgent` | `handle_data_agent` | `local_graph` | serial / n/a | none | instrumented local graph Driver |
| Analyst / `AnalystAgent` | `handle_analyst_agent` | `remote_task` | serial / n/a | none | remote work unit + Bot supervisor |
| Review / `ReviewAgent` | `handle_review_agent` | `resumable_graph` | parallel / all | action + recovery | checkpoint Driver + Bot supervisor |
| BriefGene / `BriefGeneAgent` | `handle_brief_gene_agent` | `local_graph` | hybrid / best effort | none | instrumented local graph Driver |
| DeepGenome / `DeepGenomeAgent` | `handle_deep_genome_agent` | `hybrid` | hybrid / best effort | recovery | hybrid work units + Bot supervisor |
| Research / `InSilicoResearchAgent` | `handle_in_silico_research_agent` | `hybrid` | hybrid / best effort | recovery | hybrid work units + Bot supervisor |
| Design / `DigitalDesignAgent` | `handle_digital_design_agent` | `remote_fanout` | parallel / best effort | none | fan-out work units + Bot supervisor |
| Network / `GeneNetworkAgent` | `handle_gene_network_agent` | `remote_fanout` | parallel / best effort | none | fan-out work units + Bot supervisor |

The source of truth is `src/mcp_server_phytomni/public_agent_catalog.py`.
MCP handler/schema maps, HTTP Agent maps, Expert aliases, OpenAI model maps,
remote policy sets, graph metadata, capabilities, and result-delivery sets are
derived from or drift-checked against that catalog.

## Entry-point and execution-route matrix

| Caller/route | Concrete entry point | Canonical path | Allowed responsibility | Evidence |
|---|---|---|---|---|
| Web async message | Web transaction + execution outbox | Bot `/executions` admission -> `ExecutionRuntime.start` | admit, dispatch intent, format `202` | Web convergence gate; cross-service admission tests |
| Authenticated Bot HTTP | `api/agent_runs.py` | `invoke_public_agent` | transport decode/encode only | runtime entrypoint routing tests |
| Expert routing | canonical handler selected from catalog-backed maps | `invoke_public_agent` | Agent selection before the single runtime boundary | catalog drift and all-Agent tests |
| MCP blocking/progress | `mcp/app.py` | `invoke_public_agent` | wait/progress presentation | MCP runtime tests |
| OpenAI blocking | `api/app.py` | `invoke_public_agent` | OpenAI response formatting | transport parity tests |
| OpenAI/MCP stream | `api/app.py`, `mcp/app.py` | `invoke_public_agent_stream_response` | token/progress view; iterator completion settles same execution | stream-boundary tests |
| V2 action/resume/cancel | `api/routes/executions_v2.py` | `ExecutionRuntime.resume/cancel` | authorize and submit revisioned command | action/cancel API tests |
| Legacy Chat/Review action | `api/compat.py` | `invoke_public_agent_operation` | authorize and translate the pre-V2 envelope; no legacy lifecycle write | V2 operation/idempotency tests; old rows are read-only |
| A2UI Chat/Review stream | `api/a2ui_runtime.py` | `invoke_public_agent` / `invoke_public_agent_stream_response` | A2UI presentation over the same Runtime boundary | A2UI stream/runtime tests; no A2UI audit-row writer |
| A2A blocking/stream | `api/a2a/executor.py`, `mcp/app.py` | `invoke_public_agent` / `invoke_public_agent_stream_response` | task/status presentation over one stable execution id | A2A executor/runtime/HTTP tests |
| A2A action/resume | `api/a2a/runtime.py`, `api/app.py` | `invoke_public_agent_operation` | translate the action onto the existing execution; no task-owned terminal writer | A2A operation tests; legacy tasks are read-only |
| Remote submission result | `runtime/submit_recorder.py` | current execution boundary -> `record_reserved_submissions` | attach provider child ids/output roots to the reserved Runtime row | submit-recorder all-Agent tests; missing boundary degrades and never mints a run |
| Supervisor recovery | `runtime/execution_supervisor_service_v2.py` | `ExecutionRuntime.recover/reconcile` | leased recovery and terminal settlement | supervisor contention/restart tests |
| Provider callback/poll | provider instrumentation/reconciler | durable work-unit revision -> supervisor/runtime | record one provider observation; no GET-triggered polling | provider reconciliation tests |
| Nested public Agent | shared nested-Agent instrumentation | child span in current execution | delegate to canonical child handler; no new user execution | instrumentation tests |
| Artifact/result publish | shared artifact boundary | journal target -> private owner binding -> Bot projection -> Web projector | publish only an opaque typed target; reauthorize at click time and stream the private object through the authenticated same-origin content route | target/projection/content-delivery tests |

### Agent-by-route audit

`Runtime` below means the route reserves or reuses exactly one
`ExecutionRuntime` boundary. `View` means a read/wait/format projection over
that same execution. A dash means the catalog does not advertise that
transport; adding it requires updating the catalog and its drift tests first.

| Agent | HTTP/Expert | MCP | OpenAI block/stream | A2UI resume | A2A block/stream/resume | Async provider/supervisor |
|---|---|---|---|---|---|---|
| Chat | Runtime | Runtime | Runtime | Runtime operation | Runtime/View | n/a |
| Knowledge | Runtime | Runtime | Runtime | n/a | Runtime/View | n/a |
| Data | Runtime | Runtime | — | n/a | Runtime/View | n/a |
| Analyst | Runtime | Runtime | — | n/a | Runtime/View | Runtime work unit |
| Review | Runtime | Runtime | Runtime | Runtime operation | Runtime/View | checkpoint recovery |
| BriefGene | Runtime | Runtime | Runtime | n/a | Runtime/View | n/a |
| DeepGenome | Runtime | Runtime | — | n/a | Runtime/View | Runtime work units |
| Research | Runtime | Runtime | — | n/a | Runtime operation/View | Runtime work units |
| Design | Runtime | Runtime | — | n/a | Runtime/View | Runtime fan-out |
| Network | Runtime | Runtime | — | n/a | Runtime/View | Runtime fan-out |

All streaming variants receive the boundary from the entrypoint and settle it
only when iteration completes, fails, waits for input, or is explicitly
cancelled. Disconnecting a presentation stream is not a second cancellation or
terminal path. A2UI/A2A adapters may update active presentation data, but
operation idempotency, input state, and terminal status remain Runtime-owned.

## Web product-surface matrix

Every browser submission creates one `turn-<uuid>` before issuing HTTP. The
same value is sent as `client_turn_id`, returned as `execution_id`, used as the
only live SSE key, and retained when a temporary conversation is replaced by
its durable `dialogue_id`. Moving a subscription between those conversation
containers never opens a second stream.

| Browser surface | Public Agent(s) | Command route | Live progress route |
|---|---|---|---|
| Chat instant/expert/forced Agent | Chat, Knowledge, Data, Review, BriefGene, DeepGenome and all forced selections | `POST /api/v1/conversations/:id/messages` | `GET /api/v1/executions/:execution_id/events/stream` |
| Analysis product | Analyst | `POST /api/v1/agent-products/AnalystAgent/runs` | same execution-id SSE |
| Research product | Research | `POST /api/v1/agent-products/InSilicoResearchAgent/runs` | same execution-id SSE |
| Digital Design product | Design | `POST /api/v1/agent-products/DigitalDesignAgent/runs` | same execution-id SSE |
| Gene Network product | Network | `POST /api/v1/agent-products/GeneNetworkAgent/runs` | same execution-id SSE |

The four product routes and the Chat route share the same Web `Query`
admission transaction, outbox, dispatcher, Bot runtime, journal and projector.
They differ only in route-owned input validation and presentation. A
server-minted identity is permitted solely for non-browser/internal
compatibility callers that omit the field; it still enters that same V2 path
and cannot select a synchronous execution implementation.

`/conversations/:id/runs/:run_id/...` and `/async-tasks/:id/lifecycle` remain
owner-authorized, read-only views for records created before V2. New browser
turns never attach a live run-addressed stream and never poll lifecycle once
an `execution_id` exists.

## Responsibility disposition and deletion evidence

| Responsibility | Canonical owner | Disposition and evidence | Compatibility / deadline |
|---|---|---|---|
| Agent catalog/routing policy | Bot `PublicAgentSpec` | retained; all 10 exact identities and derived maps drift-tested | no compatibility copy |
| Agent business behavior | MCP domain handlers | retained unchanged; Runtime modules are tested not to import Agent implementations | intentional behavior changes require another spec |
| Public Agent invocation | `execution_entrypoint_v2` + `ExecutionRuntime` | retained; zero-bypass scan and per-Agent reservation/Driver/journal/terminal test | blocking/streaming are views, not alternate execution |
| A2UI/A2A action and resume | `invoke_public_agent_operation` | old `claim_a2ui_action`, `complete_a2ui_action`, direct input emitters, direct `settle_run`, and fallback execution creation removed from reachable adapters | pre-V2 rows are read-only; no fallback command authority |
| Remote task submission recording | current Runtime boundary + `record_reserved_submissions` | old submit-recorder fallback `create_run` and run-id mint removed; all five remote/hybrid handlers attach children to the pre-reserved row | missing/mismatched boundaries expose degraded tracking without creating state |
| Graph/tool/provider/checkpoint/artifact observations | shared instrumentation | retained; business code contains no repeated lifecycle emitter path | private diagnostics remain separate from public summaries |
| Durable detached/recovery work | Bot supervisor and registered work units | old unregistered durable `create_task` paths removed/blocked by scanner | domain recovery adapters may only execute registered work |
| Event/span persistence | Bot V2 journal | retained as only new-execution journal writer | V1 event tables historical-read only; remove after 2026-11-30 and 30 clean production days |
| Bot V1 run-addressed event APIs | V2 projection adapter | thin read/format adapter; no independent Agent dispatch or writer | Bot runtime owner; remove after 2026-11-30 and consumer telemetry reaches zero |
| Web run-addressed event APIs | Web execution gateway | thin owner-authorized read adapter; GET purity tests assert no DB writes | Web API owner; same retention deadline as Bot V1 route |
| Web lifecycle GET | Web stored projection | pure read adapter; provider polling and reconciliation removed | Web API owner; remove after 2026-11-30 and legacy client usage reaches zero |
| Web message execution | transaction + outbox dispatcher | retained as sole normal path; request-owned settlement/fallback removed | blocking compatibility may only wait/format the admitted execution |
| Bot blocking/native run persistence | Runtime reservation + terminal settlement | V1 `record_sync_run`, `reserve_sync_run`, `settle_reserved_sync_run`, and `fail_reserved_sync_run` implementations and callers deleted; semantic resolver/dispatch failures now settle the Runtime-owned row | no compatibility writer remains |
| Remote compatibility response identity | current Runtime execution boundary | response `run_id` is the Runtime-owned run and accepted provider identities are projected from the same invocation context, including degraded V1 tracking | V1 tracking failure cannot mint or erase the public identity |
| Assistant/result settlement | Web projector + `conversation_messages_v2` | retained as sole Bot-derived message/content writer; request/read writers removed | historical aggregate rows remain readable indefinitely and receive no Bot-derived answer/projection writes |
| V2 turn identity/context | Web admission + `conversation_turns_v2` | retained as the dedicated owner-scoped turn/context record; `conversation_turn_sequences_v2` allocates the stable numeric namespace and context ACK/lock, selected Agent identity/version, execution id and message identities persist here | `question_agent_logs` is historical-read only for V2 admissions and receives no new V2 identity, context, answer, result or lifecycle writes |
| Private result delivery | Bot `execution_target_bindings_v2` + authenticated Web content proxy | retained; the journal/public API exposes only opaque target ids, while the private storage reference is owner-scoped, immutable and resolved only after click-time authorization | no public event, message, URL, error or browser payload may contain the storage reference; materialized files are confined to a one-shot temporary directory |
| Current A2UI action | V2 execution command API | retained; `execution_id` messages cannot attach or fall back to legacy transport | legacy conversation/run action only for pre-V2 messages; remove after 2026-11-30 |
| Browser execution state | execution-id V2 reducer | retained; old `useStreamMessage`/send branch files and implementation tests deleted | historical decoder may label incomplete V1 history |
| Browser liveness | one resumable SSE + sequence-free heartbeat | retained; heartbeat updates transport contact, never invents Agent activity | lifecycle polling is terminal compatibility/read fallback only |
| Timeline/Todo/Results/workspace | V2 projection + presentation reducer | retained; generic graph/root lifecycle noise hidden/coalesced, semantic work remains clickable | old records are rendered as legacy/incomplete, never rewritten |

## Business-parity and cohort gate

A cohort is complete only when all of these are true:

1. Characterization/differential fixtures preserve routing, validation,
   scientific branches and thresholds, provider/tool calls, prompts/query
   transforms, result/artifact contracts, follow-ups, and conversation effects.
2. Exactly one reachable production path performs each provider call and
   durable state transition.
3. Every declared transport reaches the Agent's catalog Driver and the common
   reservation/journal/terminal settlement path.
4. Compatibility code is bounded to authorize/read/wait/format for V2; it
   cannot be a fallback command path for a V2 execution.
5. Superseded modules, imports, routes, registrations, flags, maps, comments,
   baseline files, and implementation-specific tests are removed.
6. Static/reachability gates report zero duplicate responsibility.
7. Retained historical adapters have an owner, no-new-writes evidence, usage
   telemetry, the deadline above, and an explicit removal task.

Structural or semantic validation that occurs after a public Agent has been
selected is executed inside the Runtime boundary. A rejected selected command
therefore leaves one safe failed execution record instead of silently
disappearing or invoking a request-owned failure writer. Authentication,
ownership, payload-size, and transport-shape rejection remain pre-admission.

Production differential checks replay captured inputs or committed facts and
must be side-effect free. They never invoke providers twice.

## Local verification evidence (2026-08-21)

The reopened `unified-agent-execution-runtime` change was verified locally
after removing reachable V1 Bot-derived message/lifecycle writers,
transport-owned execution paths, and V2 admission writes to
`question_agent_logs`. V2 identity/context now resides in the dedicated
`conversation_turns_v2` record, while historical aggregate rows remain
read-only:

- Catalog/route coverage: all 10 public Agents in the matrix above enter the
  same V2 reservation, Driver and journal boundary.  A deterministic-provider
  suite invokes the real registered Handler (not a replacement callback) for
  every declared transport view, keeps schema/tool/Todo/submission formatting
  active, and proves one outer provider call per case; asynchronous Agents
  remain running until their supervisor observes a terminal provider fact.
- Bot focused execution/runtime/supervisor/provider suites: 263 passed; the
  real-handler/HTTP/progress/command/A2UI/A2A acceptance selection passed 152
  tests.
- Bot resolver, attachment, routing, contract and status suites: 203 passed
  across the focused invocations used during convergence.
- Bot static convergence scan: zero duplicate-responsibility violations and
  no reachable retired execution module or secondary Runtime writer.
- Bot lint/type gates: Ruff passed; mypy passed for 457 source files; pyright
  reported 0 errors (4 pre-existing `__all__` warnings only); source compile
  passed.
- Web API: `go test ./...` passed; public Agent catalog drift check passed;
  execution Runtime convergence scan reported zero violations.
- Result delivery: opaque public targets resolve through owner-scoped private
  bindings and an authenticated same-origin content proxy; focused Bot target
  delivery/runtime tests passed (59 tests), and focused Web gateway/client/
  handler/router tests passed.
- Cross-service: a real Web SQLite admission/outbox/projector drove a separate
  production Bot ASGI process, SQLite journal and detached command worker. It
  proved durable admission before routing, projector restart after offline
  completion, exact replay beyond the 8K snapshot bound, GET/SSE event-id
  equivalence and cached terminal replay after Bot shutdown. The test also
  found and fixed a fresh-database worker-start race by bootstrapping the V2
  command schema before the first claim.
- Browser: production build/type-check passed; 243 test files and 3,735 tests
  passed. Test-only connection-refused diagnostics for the optional port-3000
  backend did not fail the suite or its warning oracle.
- OpenSpec: strict validation of `unified-agent-execution-runtime` passed.
- Rebuilt services: Vite on 5173, Bot on 8000, and Web API on 8080 all returned
  HTTP 200 from their relevant root/health/readiness endpoints. Web startup
  also completed its Bot Agent-slug validation before binding 8080.

The complete Bot test suite is not recorded as passing on this Windows host:
environment-specific symlink privileges, POSIX permission semantics and Linux
`openat2`/`ctypes` fixtures fail here, and one unrelated slow test was
interrupted after the environmental failures were identified. Consequently,
production canary, live cross-service acceptance for every Agent, and human
business-parity sign-off remain open gates; the change must not be archived
until those gates are completed.

## Verification update (2026-08-23)

The convergence review was rerun after the real Web/Bot cross-service matrix
and public Gene Network work trace were added. The Bot scanner again reported
`violations: []`, including no direct lifecycle emitter in Agent business code;
the Network presenter now delegates safe reasoning and decision summaries to
the shared event sink. The matching real-handler, catalog, and public-producer
selection passed 99 tests. The Web scanner also reports zero violations,
exactly one execution subscription registry, and a connected output-to-message
bridge.

The real cross-service suite passed in 85.065 seconds while covering all ten
canonical handlers, fresh Bot/Web SQLite stores, async admission/outbox,
detached dispatch, projector restart, Bot outage/offline completion, exact
9,001-character replay, GET/SSE identity, reconnect, retry, best-effort cancel
with late success, partial/timeout/degraded/waiting-input outcomes, private
input resume, and serial/parallel/nested topology. Full serial Web Go and race
gates passed. The frontend full suite passed 249 files / 3,834 tests, and its
type-check, production build, format check, static-exemption gate, and focused
307-test product regression selection passed. Strict OpenSpec validation also
passed.

A fresh full Bot sweep recorded 7,162 passes and exposed portable gate defects
in zero-TTL expiry, cross-platform MCP executable validation, close/network/
SQLite inventories, an Expert-history expectation, and path-based fixtures.
After repair, the combined 162-test reproduction selection passes. The
remaining 27 last-failed cases are host/baseline exclusions: unavailable
Winsock initialization in child Python processes, Windows symlink privilege,
POSIX `fcntl` and mode-bit assertions, a non-launchable `uv` shim in one static
inventory fixture, and POSIX-shell/literal-newline commit-message fixtures.

Managed browser initialization succeeded, but the Codex URL security policy
rejected claiming or reloading the existing loopback frontend tab. Browser DOM
acceptance, production-like canary/rollback, and explicit human business-parity
approval therefore remain open; this change is not archive-ready.
