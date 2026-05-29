# Design: Web MySQL → Bot SQLite History ETL

Status: **design only — not implemented.** This note captures the
rationale, scope, source→target mapping, architecture, safety model,
and a phased roadmap for a one-time (plus optional incremental)
backfill of historical conversation/run records from the legacy
Phytomni-Web MySQL store into the Bot `runs` / `tasks` SQLite store.

The target artifact is `scripts/etl_web_mysql_history.py`. It does not
exist yet; this document is the prerequisite design so that the script,
when built, has an agreed contract rather than being reverse-engineered
from a half-finished implementation.

## 1. Why (rationale)

Under the candidate-A consumer model (see
[web-cutover-checklist.md](../ops/web-cutover-checklist.md)), Bot becomes
the single persistence owner of run/turn history that previously lived
only in Web's MySQL tables. The Bot `runs` table starts empty at
cutover, so `GET /v1/runs?dialogue_id=` returns nothing for any
conversation that predates Bot persistence.

The ETL exists to make pre-cutover history visible through the same
`/v1/runs` contract Web Go already consumes, so the history page does
not show a hard "before vs after cutover" cliff. Without it, Web Go
would have to keep a parallel read path against its old MySQL tables
indefinitely, which defeats the consolidation goal.

## 2. Scope

In scope:

- One-time backfill of historical run-equivalent rows into Bot `runs`.
- Preserving the JOIN identifiers Web Go relies on (`dialogue_id`,
  `bot_run_id`) so migrated rows remain reachable from Web user tables.
- Idempotent re-runnability (safe to run twice without duplicates).

Out of scope (explicit non-goals):

- Migrating `s_question_agent_logs` (deferred — joint schema-slimming
  work, per the checklist "Out of scope" section).
- Live dual-write or CDC streaming. If an incremental top-up is needed,
  it is a bounded "since `created_at` watermark" re-run, not a
  streaming pipeline.
- Any write back to MySQL. The ETL is strictly read-from-MySQL,
  write-to-SQLite.

## 3. Source and target

### 3.1 Target (authoritative, known)

Bot `runs` table — DDL in
[`runtime/run_registry.py`](../../src/mcp_server_phytomni/runtime/run_registry.py)
(`_CREATE_RUNS_DDL`):

| Column         | Type | Notes                                          |
| -------------- | ---- | ---------------------------------------------- |
| `run_id`       | TEXT | PRIMARY KEY. Must be minted via `IdFactory`.   |
| `user_id`      | TEXT | NOT NULL. `"web"` under candidate A.           |
| `agent`        | TEXT | NOT NULL. Canonical slug (`chat`, `analyst`…). |
| `origin`       | TEXT | NOT NULL. New value `"web_etl"` proposed.      |
| `status`       | TEXT | NOT NULL. Terminal (`succeeded` / `failed`).   |
| `result_json`  | TEXT | Stored answer envelope, nullable.              |
| `error`        | TEXT | Nullable.                                      |
| `created_at`   | TEXT | NOT NULL. ISO-8601 UTC (sorts lexically).      |
| `updated_at`   | TEXT | NOT NULL. ISO-8601 UTC.                        |
| `expires_at`   | TEXT | Nullable. **Leave NULL** so TTL GC never       |
|                |      | reaps backfilled history.                      |
| `dialogue_id`  | TEXT | JOIN key for Web Go. Carry through verbatim.   |
| `query`        | TEXT | User question text.                            |
| `tool_name`    | TEXT | Historical Web alias (kept as-is for display). |
| `model`        | TEXT | Nullable.                                      |
| `request_json` | TEXT | Nullable.                                      |

The `tasks` table (with its `task_log` column) is **not** a backfill
target in the in-scope cut: pre-cutover analyst logs live in
`s_question_agent_logs`, which is explicitly deferred.

### 3.2 Source (NEEDS CONFIRMATION)

The Web MySQL schema is owned by `nky_client_go/model/table.go` and the
`s_*` tables (`s_dialogue_owner`, `s_user_turn_reactions`, and the
history table holding per-turn questions/answers). The exact column
names and the canonical "one historical run" grain MUST be confirmed
against `table.go` before implementation — this design deliberately
does not invent them.

Open mapping questions to resolve against `table.go`:

- Which table + columns hold the per-turn `(question, answer, created_at, dialogue_id, tool/agent, model)` tuple?
- Is there a stable historical run identifier to map onto `bot_run_id`,
  or must the ETL synthesize one deterministically (see §5)?
- How is agent/tool recorded on the Web side, and what is the
  alias→canonical-slug mapping (reuse `/v1/agents.legacy_aliases`)?

## 4. Architecture

```text
 MySQL (read-only)            ETL process                 SQLite (Bot)
 ┌───────────────┐   batched  ┌──────────────────────┐   INSERT OR     ┌──────────┐
 │ s_* history   │──cursor───▶│ extract → map → load │──IGNORE────────▶│  runs    │
 │ tables        │  (LIMIT/   │  (per-row transform) │  (deterministic │          │
 └───────────────┘   OFFSET)  └──────────────────────┘   run_id)       └──────────┘
```

- **Extract**: read-only MySQL connection, server-side cursor or
  keyset pagination on `created_at` (NOT `LIMIT/OFFSET` over millions of
  rows — keyset avoids the deep-offset scan). Read in bounded batches.
- **Transform**: pure function `web_row → RunSpec + RunOutcome + RunRequestInfo`. Alias→slug resolution reuses the same mapping
  `/v1/agents.legacy_aliases` exposes, so there is one source of truth.
- **Load**: reuse `RunRegistry` insert helpers where possible rather
  than hand-writing SQL, so the ETL cannot drift from the live DDL. If
  `RunRegistry.create_run` cannot express "insert with a caller-chosen
  `created_at` and NULL `expires_at`", add a narrow `backfill_run()`
  seam to `RunRegistry` rather than bypassing it with raw SQL.

The script runs as a standalone operator tool (like
[`scripts/encrypt_env.py`](../../scripts/encrypt_env.py)): explicit
`--mysql-dsn`, `--tasks-db`, `--since`, `--dry-run`, `--batch-size`
arguments, no implicit env coupling beyond what `ServerConfig` already
provides.

## 5. Idempotency and safety

- **Deterministic `run_id`**: the migrated `run_id` MUST be a pure
  function of a stable Web key (e.g.
  `IdFactory`-formatted hash of `(dialogue_id, web_turn_id)`), never a
  fresh random id. This makes the load `INSERT OR IGNORE` (or
  `INSERT … ON CONFLICT(run_id) DO NOTHING`) genuinely idempotent: a
  second run inserts zero duplicate rows.
- **No TTL reaping**: backfilled rows set `expires_at = NULL` so the
  lazy GC (`purge_expired`) never deletes history.
- **`--dry-run`**: default to counting + sampling the transform output
  without writing, so an operator can eyeball the mapping before
  committing.
- **Watermark for incremental top-up**: persist the max migrated
  `created_at`; a re-run with `--since <watermark>` only pulls newer
  rows. Combined with deterministic ids this is safe even if the window
  overlaps.
- **Read-only on MySQL**: the DSN user should have SELECT-only grants;
  the ETL issues no writes to the source.
- **Local SQLite only**: the target DB path stays on a local
  filesystem (SQLite WAL deadlocks on NFS — same constraint the live
  service documents).

## 6. Roadmap (phasing)

1. **Confirm source schema**: read `nky_client_go/model/table.go`,
   produce the concrete source→target column map, and settle the
   open questions in §3.2. Gate: a reviewed mapping table.
1. **`RunRegistry` backfill seam**: add `backfill_run()` (or confirm
   `create_run` suffices) with unit tests for caller-chosen
   `created_at` + NULL `expires_at` + deterministic-id conflict
   no-op. Gate: tests green.
1. **Transform unit**: pure `web_row → Run*` function with table-driven
   tests over representative historical rows (success, failure, missing
   model, unknown alias). Gate: tests green, alias map reuses
   `legacy_aliases`.
1. **Script + dry-run**: wire extract/load around the transform with
   `--dry-run` and keyset pagination. Gate: dry-run over a MySQL
   snapshot prints expected counts.
1. **Bounded live backfill**: run against a copy/snapshot first, verify
   `GET /v1/runs?dialogue_id=` surfaces migrated rows with intact JOIN
   keys, then run against production with `--dry-run` removed.

## 7. Testing strategy

- Unit: the transform function (pure, no I/O) under
  `tests/` with fixture rows — the bulk of correctness lives here.
- Integration (opt-in, `integration` marker): a temp SQLite target +
  a small seeded MySQL (or a fake cursor) proving idempotent re-run
  inserts zero duplicates and `expires_at` stays NULL.
- The live MySQL extract is **not** part of the offline gate (mirrors
  the `e2e/` one-layer drop): it runs only in an operator-driven,
  network-allowed context.

## 8. Open decisions for product/owner

- Is the backfill in the binding `.cursor` plan scope (mandatory) or
  does the 2026-05-24 "deferred" status stand? This design is ready
  either way but does not presume the answer.
- New `origin` value `"web_etl"` vs reusing `"local"`: a distinct
  origin lets `/v1/runs?origin=` filter pre-cutover history out, at the
  cost of one more enum value Web Go must tolerate.
