# Execution Runtime Initial Bounds

These defaults preserve the measured/implemented V1 limits where evidence
exists and make new V2 values explicit. They are configuration defaults, not
scientific behavior. Staging evidence must replace estimates before broad
activation.

- **Bound:** Public event size
  - **Initial value:** 16 KiB
  - **Evidence / rationale:** existing V1 `max_event_bytes`
- **Bound:** Public summary
  - **Initial value:** 512 characters
  - **Evidence / rationale:** existing V1 safe-summary limit
- **Bound:** Todo items
  - **Initial value:** 100
  - **Evidence / rationale:** existing V1 contract
- **Bound:** Default/max event page
  - **Initial value:** 50 / 200
  - **Evidence / rationale:** existing V1 API contract
- **Bound:** Retained events per execution
  - **Initial value:** 10,000
  - **Evidence / rationale:** existing V1 run retention
- **Bound:** Live durable backlog
  - **Initial value:** 1,000
  - **Evidence / rationale:** existing V1 follow bound
- **Bound:** Informational progress coalescing
  - **Initial value:** 500 ms
  - **Evidence / rationale:** existing V1 producer bound
- **Bound:** SSE heartbeat
  - **Initial value:** 15 s
  - **Evidence / rationale:** long enough to avoid event churn; short enough to
    expose a live connection
- **Bound:** Web dispatch lease
  - **Initial value:** 30 s
  - **Evidence / rationale:** initial estimate; renew before half-life
- **Bound:** Web projection lease
  - **Initial value:** 30 s
  - **Evidence / rationale:** initial estimate; independent from dispatch lease
- **Bound:** Bot supervisor lease
  - **Initial value:** 30 s
  - **Evidence / rationale:** initial estimate; work-unit operations remain
    idempotent
- **Bound:** Initial retry backoff
  - **Initial value:** 1 s
  - **Evidence / rationale:** bounded exponential retry starting point
- **Bound:** Maximum retry backoff
  - **Initial value:** 60 s
  - **Evidence / rationale:** prevents hot loops during provider outage
- **Bound:** Message snapshot interval
  - **Initial value:** 2 s or 4 KiB
  - **Evidence / rationale:** whichever occurs first; transient tokens are not
    journal facts
- **Bound:** Browser reconnect attempts
  - **Initial value:** unbounded while execution is non-terminal, capped at 60
    s delay
  - **Evidence / rationale:** verified history stays visible

## Dispatch reconciliation and operator ownership

The detached Bot command queue currently permits five bounded invocation
attempts. Web permits six delivery attempts with exponential delays capped at
60 seconds. A delivery timeout or connection loss is not proof that Bot did not
reserve the execution: once the blind-delivery budget is exhausted the command
enters `reconcile` and remains non-terminal. Reconciliation addresses Bot by
owner plus public `execution_id`; it does not require the lost acknowledgement
or `bot_run_id`.

Unresolved delivery uses the catalog execution deadline as its outer truth
bound. The current Agent defaults are 300 seconds for Chat, 600 seconds for
Knowledge and Data, 900 seconds for BriefGene, 1,800 seconds for Review, 3,600
seconds for Analyst, and 7,200 seconds for DeepGenome, Research, Design, and
Network. Before that deadline, delivery exhaustion produces degraded tracking
and operator diagnostics, not a user-visible scientific failure. The Bot
supervisor owns truthful execution timeout/terminal settlement; an authorized
Web admin or super-admin owns retry/reconcile/dead-letter intervention under
revision fences.

Operational review uses dispatch claimed/retried/reconcile/rejected counts,
degraded projection count, oldest reconciliation time, projection lag, lease
steals, and terminal-conflict suppression. `first_error_code` preserves the
initial actionable bounded cause, `last_error_code` records the latest bounded
observation, and private exception text or payloads are never persisted.

The V1 baseline contains no reliable production distribution for task duration,
provider polling, output update rate, or database contention. Task 14.9 must
record staging p50/p95/p99 values, provider idempotency/cancellation
guarantees, and any tuned limits before broad activation. Until then V2 remains
capability gated.
