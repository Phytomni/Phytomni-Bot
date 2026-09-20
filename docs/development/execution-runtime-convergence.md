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

Both scanners must report empty responsibility arrays and `violations: []`. The
Bot scanner also rejects secondary runtime writers in transport adapters and
prevents retired A2UI/background lifecycle modules from being restored.

## Canonical public-Agent matrix

All rows use `dispatch_adapter=execution_runtime` and
`event_contract=execution_journal_v2`. The handler named below remains the one
business implementation; Drivers wrap it and do not copy its decisions.

- **Agent/tool:** Chat / `ChatAgent`
  - **Canonical handler:** `handle_chat_agent`
  - **Driver:** `resumable_graph`
  - **Topology / join:** conditional / n/a
  - **Resume:** action + recovery
  - **Primary side-effect owner:** checkpoint Driver + Bot supervisor
- **Agent/tool:** Knowledge / `KnowledgeAgent`
  - **Canonical handler:** `handle_knowledge_agent`
  - **Driver:** `local_graph`
  - **Topology / join:** conditional / n/a
  - **Resume:** none
  - **Primary side-effect owner:** instrumented local graph Driver
- **Agent/tool:** Data / `DataAgent`
  - **Canonical handler:** `handle_data_agent`
  - **Driver:** `local_graph`
  - **Topology / join:** serial / n/a
  - **Resume:** none
  - **Primary side-effect owner:** instrumented local graph Driver
- **Agent/tool:** Analyst / `AnalystAgent`
  - **Canonical handler:** `handle_analyst_agent`
  - **Driver:** `remote_task`
  - **Topology / join:** serial / n/a
  - **Resume:** none
  - **Primary side-effect owner:** remote work unit + Bot supervisor
- **Agent/tool:** Review / `ReviewAgent`
  - **Canonical handler:** `handle_review_agent`
  - **Driver:** `resumable_graph`
  - **Topology / join:** parallel / all
  - **Resume:** action + recovery
  - **Primary side-effect owner:** checkpoint Driver + Bot supervisor
- **Agent/tool:** BriefGene / `BriefGeneAgent`
  - **Canonical handler:** `handle_brief_gene_agent`
  - **Driver:** `local_graph`
  - **Topology / join:** hybrid / best effort
  - **Resume:** none
  - **Primary side-effect owner:** instrumented local graph Driver
- **Agent/tool:** DeepGenome / `DeepGenomeAgent`
  - **Canonical handler:** `handle_deep_genome_agent`
  - **Driver:** `hybrid`
  - **Topology / join:** hybrid / best effort
  - **Resume:** recovery
  - **Primary side-effect owner:** hybrid work units + Bot supervisor
- **Agent/tool:** Research / `InSilicoResearchAgent`
  - **Canonical handler:** `handle_in_silico_research_agent`
  - **Driver:** `hybrid`
  - **Topology / join:** hybrid / best effort
  - **Resume:** recovery
  - **Primary side-effect owner:** hybrid work units + Bot supervisor
- **Agent/tool:** Design / `DigitalDesignAgent`
  - **Canonical handler:** `handle_digital_design_agent`
  - **Driver:** `remote_fanout`
  - **Topology / join:** parallel / best effort
  - **Resume:** none
  - **Primary side-effect owner:** fan-out work units + Bot supervisor
- **Agent/tool:** Network / `GeneNetworkAgent`
  - **Canonical handler:** `handle_gene_network_agent`
  - **Driver:** `remote_fanout`
  - **Topology / join:** parallel / best effort
  - **Resume:** none
  - **Primary side-effect owner:** fan-out work units + Bot supervisor

The source of truth is `src/mcp_server_phytomni/public_agent_catalog.py`. MCP
handler/schema maps, HTTP Agent maps, Expert aliases, OpenAI model maps, remote
policy sets, graph metadata, capabilities, and result-delivery sets are derived
from or drift-checked against that catalog.

## Entry-point and execution-route matrix

- **Caller/route:** Web async message
  - **Concrete entry point:** Web transaction + execution outbox
  - **Canonical path:** Bot `/executions` admission -> `ExecutionRuntime.start`
  - **Allowed responsibility:** admit, dispatch intent, format `202`
  - **Evidence:** Web convergence gate; cross-service admission tests
- **Caller/route:** Authenticated Bot HTTP
  - **Concrete entry point:** `api/agent_runs.py`
  - **Canonical path:** `invoke_public_agent`
  - **Allowed responsibility:** transport decode/encode only
  - **Evidence:** runtime entrypoint routing tests
- **Caller/route:** Expert routing
  - **Concrete entry point:** canonical handler selected from catalog-backed
    maps
  - **Canonical path:** `invoke_public_agent`
  - **Allowed responsibility:** Agent selection before the single runtime
    boundary
  - **Evidence:** catalog drift and all-Agent tests
- **Caller/route:** MCP blocking/progress
  - **Concrete entry point:** `mcp/app.py`
  - **Canonical path:** `invoke_public_agent`
  - **Allowed responsibility:** wait/progress presentation
  - **Evidence:** MCP runtime tests
- **Caller/route:** OpenAI blocking
  - **Concrete entry point:** `api/app.py`
  - **Canonical path:** `invoke_public_agent`
  - **Allowed responsibility:** OpenAI response formatting
  - **Evidence:** transport parity tests
- **Caller/route:** OpenAI/MCP stream
  - **Concrete entry point:** `api/app.py`, `mcp/app.py`
  - **Canonical path:** `invoke_public_agent_stream_response`
  - **Allowed responsibility:** token/progress view; iterator completion
    settles same execution
  - **Evidence:** stream-boundary tests
- **Caller/route:** V2 action/resume/cancel
  - **Concrete entry point:** `api/routes/executions_v2.py`
  - **Canonical path:** `ExecutionRuntime.resume/cancel`
  - **Allowed responsibility:** authorize and submit revisioned command
  - **Evidence:** action/cancel API tests
- **Caller/route:** Legacy Chat/Review action
  - **Concrete entry point:** `api/compat.py`
  - **Canonical path:** `invoke_public_agent_operation`
  - **Allowed responsibility:** authorize and translate the pre-V2 envelope; no
    legacy lifecycle write
  - **Evidence:** V2 operation/idempotency tests; old rows are read-only
- **Caller/route:** A2UI Chat/Review stream
  - **Concrete entry point:** `api/a2ui_runtime.py`
  - **Canonical path:** `invoke_public_agent` /
    `invoke_public_agent_stream_response`
  - **Allowed responsibility:** A2UI presentation over the same Runtime
    boundary
  - **Evidence:** A2UI stream/runtime tests; no A2UI audit-row writer
- **Caller/route:** A2A blocking/stream
  - **Concrete entry point:** `api/a2a/executor.py`, `mcp/app.py`
  - **Canonical path:** `invoke_public_agent` /
    `invoke_public_agent_stream_response`
  - **Allowed responsibility:** task/status presentation over one stable
    execution id
  - **Evidence:** A2A executor/runtime/HTTP tests
- **Caller/route:** A2A action/resume
  - **Concrete entry point:** `api/a2a/runtime.py`, `api/app.py`
  - **Canonical path:** `invoke_public_agent_operation`
  - **Allowed responsibility:** translate the action onto the existing
    execution; no task-owned terminal writer
  - **Evidence:** A2A operation tests; legacy tasks are read-only
- **Caller/route:** Remote submission result
  - **Concrete entry point:** `runtime/submit_recorder.py`
  - **Canonical path:** current execution boundary ->
    `record_reserved_submissions`
  - **Allowed responsibility:** attach provider child ids/output roots to the
    reserved Runtime row
  - **Evidence:** submit-recorder all-Agent tests; missing boundary degrades
    and never mints a run
- **Caller/route:** Supervisor recovery
  - **Concrete entry point:** `runtime/execution_supervisor_service_v2.py`
  - **Canonical path:** `ExecutionRuntime.recover/reconcile`
  - **Allowed responsibility:** leased recovery and terminal settlement
  - **Evidence:** supervisor contention/restart tests
- **Caller/route:** Provider callback/poll
  - **Concrete entry point:** provider instrumentation/reconciler
  - **Canonical path:** durable work-unit revision -> supervisor/runtime
  - **Allowed responsibility:** record one provider observation; no
    GET-triggered polling
  - **Evidence:** provider reconciliation tests
- **Caller/route:** Nested public Agent
  - **Concrete entry point:** shared nested-Agent instrumentation
  - **Canonical path:** child span in current execution
  - **Allowed responsibility:** delegate to canonical child handler; no new
    user execution
  - **Evidence:** instrumentation tests
- **Caller/route:** Artifact/result publish
  - **Concrete entry point:** shared artifact boundary
  - **Canonical path:** journal target -> private owner binding -> Bot
    projection -> Web projector
  - **Allowed responsibility:** publish only an opaque typed target;
    reauthorize at click time and stream the private object through the
    authenticated same-origin content route
  - **Evidence:** target/projection/content-delivery tests

### Agent-by-route audit

`Runtime` below means the route reserves or reuses exactly one
`ExecutionRuntime` boundary. `View` means a read/wait/format projection over
that same execution. A dash means the catalog does not advertise that
transport; adding it requires updating the catalog and its drift tests first.

- **Agent:** Chat
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** Runtime
  - **A2UI resume:** Runtime operation
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** n/a
- **Agent:** Knowledge
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** Runtime
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** n/a
- **Agent:** Data
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** n/a
- **Agent:** Analyst
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** Runtime work unit
- **Agent:** Review
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** Runtime
  - **A2UI resume:** Runtime operation
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** checkpoint recovery
- **Agent:** BriefGene
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** Runtime
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** n/a
- **Agent:** DeepGenome
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** Runtime work units
- **Agent:** Research
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime operation/View
  - **Async provider/supervisor:** Runtime work units
- **Agent:** Design
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** Runtime fan-out
- **Agent:** Network
  - **HTTP/Expert:** Runtime
  - **MCP:** Runtime
  - **OpenAI block/stream:** —
  - **A2UI resume:** n/a
  - **A2A block/stream/resume:** Runtime/View
  - **Async provider/supervisor:** Runtime fan-out

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

- **Browser surface:** Chat instant/expert/forced Agent
  - **Public Agent(s):** Chat, Knowledge, Data, Review, BriefGene, DeepGenome
    and all forced selections
  - **Command route:** `POST /api/v1/conversations/:id/messages`
  - **Live progress route:**
    `GET /api/v1/executions/:execution_id/events/stream`
- **Browser surface:** Analysis product
  - **Public Agent(s):** Analyst
  - **Command route:** `POST /api/v1/agent-products/AnalystAgent/runs`
  - **Live progress route:** same execution-id SSE
- **Browser surface:** Research product
  - **Public Agent(s):** Research
  - **Command route:** `POST /api/v1/agent-products/InSilicoResearchAgent/runs`
  - **Live progress route:** same execution-id SSE
- **Browser surface:** Digital Design product
  - **Public Agent(s):** Design
  - **Command route:** `POST /api/v1/agent-products/DigitalDesignAgent/runs`
  - **Live progress route:** same execution-id SSE
- **Browser surface:** Gene Network product
  - **Public Agent(s):** Network
  - **Command route:** `POST /api/v1/agent-products/GeneNetworkAgent/runs`
  - **Live progress route:** same execution-id SSE

The four product routes and the Chat route share the same Web `Query` admission
transaction, outbox, dispatcher, Bot runtime, journal and projector. They
differ only in route-owned input validation and presentation. A server-minted
identity is permitted solely for non-browser/internal compatibility callers
that omit the field; it still enters that same V2 path and cannot select a
synchronous execution implementation.

`/conversations/:id/runs/:run_id/...` and `/async-tasks/:id/lifecycle` remain
owner-authorized, read-only views for records created before V2. New browser
turns never attach a live run-addressed stream and never poll lifecycle once an
`execution_id` exists.

## Responsibility disposition and deletion evidence

- **Responsibility:** Agent catalog/routing policy
  - **Canonical owner:** Bot `PublicAgentSpec`
  - **Disposition and evidence:** retained; all 10 exact identities and derived
    maps drift-tested
  - **Compatibility / deadline:** no compatibility copy
- **Responsibility:** Agent business behavior
  - **Canonical owner:** MCP domain handlers
  - **Disposition and evidence:** retained unchanged; Runtime modules are
    tested not to import Agent implementations
  - **Compatibility / deadline:** intentional behavior changes require another
    spec
- **Responsibility:** Public Agent invocation
  - **Canonical owner:** `execution_entrypoint_v2` + `ExecutionRuntime`
  - **Disposition and evidence:** retained; zero-bypass scan and per-Agent
    reservation/Driver/journal/terminal test
  - **Compatibility / deadline:** blocking/streaming are views, not alternate
    execution
- **Responsibility:** A2UI/A2A action and resume
  - **Canonical owner:** `invoke_public_agent_operation`
  - **Disposition and evidence:** old `claim_a2ui_action`,
    `complete_a2ui_action`, direct input emitters, direct `settle_run`, and
    fallback execution creation removed from reachable adapters
  - **Compatibility / deadline:** pre-V2 rows are read-only; no fallback
    command authority
- **Responsibility:** Remote task submission recording
  - **Canonical owner:** current Runtime boundary +
    `record_reserved_submissions`
  - **Disposition and evidence:** old submit-recorder fallback `create_run` and
    run-id mint removed; all five remote/hybrid handlers attach children to the
    pre-reserved row
  - **Compatibility / deadline:** missing/mismatched boundaries expose degraded
    tracking without creating state
- **Responsibility:** Graph/tool/provider/checkpoint/artifact observations
  - **Canonical owner:** shared instrumentation
  - **Disposition and evidence:** retained; business code contains no repeated
    lifecycle emitter path
  - **Compatibility / deadline:** private diagnostics remain separate from
    public summaries
- **Responsibility:** Durable detached/recovery work
  - **Canonical owner:** Bot supervisor and registered work units
  - **Disposition and evidence:** old unregistered durable `create_task` paths
    removed/blocked by scanner
  - **Compatibility / deadline:** domain recovery adapters may only execute
    registered work
- **Responsibility:** Event/span persistence
  - **Canonical owner:** Bot V2 journal
  - **Disposition and evidence:** retained as only new-execution journal writer
  - **Compatibility / deadline:** V1 event tables historical-read only; remove
    after 2026-11-30 and 30 clean production days
- **Responsibility:** Bot V1 run-addressed event APIs
  - **Canonical owner:** V2 projection adapter
  - **Disposition and evidence:** thin read/format adapter; no independent
    Agent dispatch or writer
  - **Compatibility / deadline:** Bot runtime owner; remove after 2026-11-30
    and consumer telemetry reaches zero
- **Responsibility:** Web run-addressed event APIs
  - **Canonical owner:** Web execution gateway
  - **Disposition and evidence:** thin owner-authorized read adapter; GET
    purity tests assert no DB writes
  - **Compatibility / deadline:** Web API owner; same retention deadline as Bot
    V1 route
- **Responsibility:** Web lifecycle GET
  - **Canonical owner:** Web stored projection
  - **Disposition and evidence:** pure read adapter; provider polling and
    reconciliation removed
  - **Compatibility / deadline:** Web API owner; remove after 2026-11-30 and
    legacy client usage reaches zero
- **Responsibility:** Web message execution
  - **Canonical owner:** transaction + outbox dispatcher
  - **Disposition and evidence:** retained as sole normal path; request-owned
    settlement/fallback removed
  - **Compatibility / deadline:** blocking compatibility may only wait/format
    the admitted execution
- **Responsibility:** Bot blocking/native run persistence
  - **Canonical owner:** Runtime reservation + terminal settlement
  - **Disposition and evidence:** V1 `record_sync_run`, `reserve_sync_run`,
    `settle_reserved_sync_run`, and `fail_reserved_sync_run` implementations
    and callers deleted; semantic resolver/dispatch failures now settle the
    Runtime-owned row
  - **Compatibility / deadline:** no compatibility writer remains
- **Responsibility:** Remote compatibility response identity
  - **Canonical owner:** current Runtime execution boundary
  - **Disposition and evidence:** response `run_id` is the Runtime-owned run
    and accepted provider identities are projected from the same invocation
    context, including degraded V1 tracking
  - **Compatibility / deadline:** V1 tracking failure cannot mint or erase the
    public identity
- **Responsibility:** Assistant/result settlement
  - **Canonical owner:** Web projector + `conversation_messages_v2`
  - **Disposition and evidence:** retained as sole Bot-derived message/content
    writer; request/read writers removed
  - **Compatibility / deadline:** historical aggregate rows remain readable
    indefinitely and receive no Bot-derived answer/projection writes
- **Responsibility:** V2 turn identity/context
  - **Canonical owner:** Web admission + `conversation_turns_v2`
  - **Disposition and evidence:** retained as the dedicated owner-scoped
    turn/context record; `conversation_turn_sequences_v2` allocates the stable
    numeric namespace and context ACK/lock, selected Agent identity/version,
    execution id and message identities persist here
  - **Compatibility / deadline:** `question_agent_logs` is historical-read only
    for V2 admissions and receives no new V2 identity, context, answer, result
    or lifecycle writes
- **Responsibility:** Private result delivery
  - **Canonical owner:** Bot `execution_target_bindings_v2` + authenticated Web
    content proxy
  - **Disposition and evidence:** retained; the journal/public API exposes only
    opaque target ids, while the private storage reference is owner-scoped,
    immutable and resolved only after click-time authorization
  - **Compatibility / deadline:** no public event, message, URL, error or
    browser payload may contain the storage reference; materialized files are
    confined to a one-shot temporary directory
- **Responsibility:** Current A2UI action
  - **Canonical owner:** V2 execution command API
  - **Disposition and evidence:** retained; `execution_id` messages cannot
    attach or fall back to legacy transport
  - **Compatibility / deadline:** legacy conversation/run action only for
    pre-V2 messages; remove after 2026-11-30
- **Responsibility:** Browser execution state
  - **Canonical owner:** execution-id V2 reducer
  - **Disposition and evidence:** retained; old `useStreamMessage`/send branch
    files and implementation tests deleted
  - **Compatibility / deadline:** historical decoder may label incomplete V1
    history
- **Responsibility:** Browser liveness
  - **Canonical owner:** one resumable SSE + sequence-free heartbeat
  - **Disposition and evidence:** retained; heartbeat updates transport
    contact, never invents Agent activity
  - **Compatibility / deadline:** lifecycle polling is terminal
    compatibility/read fallback only
- **Responsibility:** Timeline/Todo/Results/workspace
  - **Canonical owner:** V2 projection + presentation reducer
  - **Disposition and evidence:** retained; generic graph/root lifecycle noise
    hidden/coalesced, semantic work remains clickable
  - **Compatibility / deadline:** old records are rendered as
    legacy/incomplete, never rewritten

## Business-parity and cohort gate

A cohort is complete only when all of these are true:

1. Characterization/differential fixtures preserve routing, validation,
   scientific branches and thresholds, provider/tool calls, prompts/query
   transforms, result/artifact contracts, follow-ups, and conversation effects.
1. Exactly one reachable production path performs each provider call and
   durable state transition.
1. Every declared transport reaches the Agent's catalog Driver and the common
   reservation/journal/terminal settlement path.
1. Compatibility code is bounded to authorize/read/wait/format for V2; it
   cannot be a fallback command path for a V2 execution.
1. Superseded modules, imports, routes, registrations, flags, maps, comments,
   baseline files, and implementation-specific tests are removed.
1. Static/reachability gates report zero duplicate responsibility.
1. Retained historical adapters have an owner, no-new-writes evidence, usage
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
  same V2 reservation, Driver and journal boundary. A deterministic-provider
  suite invokes the real registered Handler (not a replacement callback) for
  every declared transport view, keeps schema/tool/Todo/submission formatting
  active, and proves one outer provider call per case; asynchronous Agents
  remain running until their supervisor observes a terminal provider fact.
- Bot focused execution/runtime/supervisor/provider suites: 263 passed; the
  real-handler/HTTP/progress/command/A2UI/A2A acceptance selection passed 152
  tests.
- Bot resolver, attachment, routing, contract and status suites: 203 passed
  across the focused invocations used during convergence.
- Bot static convergence scan: zero duplicate-responsibility violations and no
  reachable retired execution module or secondary Runtime writer.
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
