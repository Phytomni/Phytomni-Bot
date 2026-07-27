# DataAgent cDNA Incident Ledger

This is an evidence-only ledger. It does not authorize a live replay, a
DataAgent behavior change, or a historical Analyst write.

## Identity

- Exact query: `What is cDNA sequence of Os09t0241100-01 in rice?`
- Server dialogue ID: `932a5dc9-d928-481f-83cc-9346dc990dda`
- Screenshot local time: `2026-07-23 21:10:23`
- Screenshot timezone: `Unknown`
- Bot request ID: `Unknown`
- Current root-cause status: `Needs Verification`

## Repository Snapshot

- Branch: `release/0.1.4`
- Bot SHA before this evidence commit:
  `c1ff3c1cc7c71ad3792a4eb4724c05cb9d5b3463`
- Handoff/spec hashes:
  - `2026-07-21-real-user-feedback-bot-handoff.md`:
    `f146704e3aeeae043434772f7961bdb48c7caac1fb36f9a123b7150440974e8d`
  - `2026-07-24-dataagent-bot-handoff.md`:
    `53a5cc2d7efcf68de23a4c90e0fc89882392784b4a6e793dbd4ff293dcb017ff`
  - `2026-07-24-bot-contract-convergence-spec.md`:
    `ea8ff5439a7c605cf545d84d49c6c8c9928f4c1bfa80a477dcfc9a5994da6d6e`

## Confirmed

- The captured Web request ended as HTTP 500.
- The known Web request-shaping and identity fixes do not prove Bot success.
- No authorized exact-query replay has been run on this SHA.

## Not Established

- The first failing Bot stage.
- Whether the transcript exists in the configured database.
- The authoritative sequence length and hash.
- Whether failure is deterministic on the current SHA.
- A usable Bot request ID for the captured incident.

## Excluded As Standalone Root Cause

- The separate Web dialogue reconciliation collision.

## Analyst Evidence

- Web request ID: `bdda4801-3ba9-4692-8d16-ad9807a6674d`
- Bot request ID: `Unknown`
- Native run ID: `Unknown`
- Task ID: `Unknown`
- `update_log` request/outcome: `Unknown`
- Reload/result projection: `Unknown`
- Historical write authorization: `Rejected by default`
- Correlation status: `Needs Verification`

## Root-Cause Gate

- Controlled exact replay: `External Pending`
- Complete safe stage trace: `External Pending`
- First failing boundary: `Needs Verification`
- Same-cause red test: `Needs Verification`
- Adjacent candidates excluded: `Needs Verification`

The gate remains stopped until an operator authorizes one controlled replay
and provides matching payload-free stage records. No behavior fix or
transcript synthesis is allowed before that evidence identifies one first
failure and a same-cause regression.

## Historical Mutation Boundary

Historical Analyst repair is an L2 operation outside this application. This
ledger authorizes no database mutation, automatic cleanup, or inferred
correlation. A future repair requires owner-scoped exact identity, a
pre-write snapshot, an audit record, an executable reversal, and explicit
human approval.

## Read-Only Historical Repair Packet

The Bot now ships `scripts/analyst_history_repair_probe.py` as a dry-run
correlation tool. It opens the registry with SQLite URI `mode=ro` and a
mutation-denying authorizer. It accepts only the fixed Web request identity
above, the owner, and optional evidence-backed Bot request / dialogue / run
identifiers. It never treats query text, titles, output paths, or a foreign
owner row as correlation evidence.

The packet reports `zero_matches`, `unique_match`, or `multiple_matches` and
always carries `write_authorized: false`. A unique result includes exact run
and task ids, a hash of the complete private pre-write snapshot, public-field
mutation and reversal proposals, and the required L2 approval roles. The
private snapshot is optional and must be written to an operator-selected
protected path outside this repository; the tracked ledger stores only its
hash and location class.

Offline implementation evidence:

- Script: `scripts/analyst_history_repair_probe.py`
- Tests: `tests/unit/scripts/test_analyst_history_repair_probe.py`
- Historical execution: `External Pending` because the Bot request id is
  still `Unknown` and no owner-approved read-only historical execution has
  been supplied.

No historical row was read or changed while this implementation was added.
