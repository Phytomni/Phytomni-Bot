# Phytomni-Bot Compatibility Register

These bridges are temporary projections, not alternate sources of truth. Each
row remains active until its owner supplies the evidence in its exit condition.
Initial review date: `2026-08-01`.

## Compatibility register

- **Bridge:** `run_id alias`
  **Canonical replacement:** `id` as primary run identity
  **External owner:** Web and Go owners
  **Known consumers:** Web history and Go gateway
  **Status:** External Pending
  **Blocker evidence:** paired SHA and staging body absent
  **Risk:** duplicate identity parsing
  **Review date:** `2026-08-01`
  **Exit condition:** Web and Go consume byte-identical `id` with matching
  fixture hashes
  **Rollback:** retain alias

- **Bridge:** `formatted execution fields`
  **Canonical replacement:** `result.execution`
  **External owner:** Web owner
  **Known consumers:** Web history/detail consumers
  **Status:** External Pending
  **Blocker evidence:** canonical consumer evidence absent
  **Risk:** operational fields may be dropped
  **Review date:** `2026-08-01`
  **Exit condition:** Web reads tracking/warnings/tasks/artifacts from canonical
  projection
  **Rollback:** retain additive fields

- **Bridge:** `DeepGenome formatted report metadata`
  **Canonical replacement:** `result.execution.report` descriptor
  **External owner:** Web and Go owners
  **Known consumers:** DeepGenome report views
  **Status:** External Pending
  **Blocker evidence:** consumer migration and staging evidence absent
  **Risk:** report metadata divergence
  **Review date:** `2026-08-01`
  **Exit condition:** consumers read the bounded canonical report descriptor
  **Rollback:** retain old projection

- **Bridge:** `top-level degraded_tracking`
  **Canonical replacement:** `result.execution.tracking.degraded`
  **External owner:** Web and Go owners
  **Known consumers:** remote-run status consumers
  **Status:** External Pending
  **Blocker evidence:** consumer field migration absent
  **Risk:** degraded state may be hidden
  **Review date:** `2026-08-01`
  **Exit condition:** paired consumers read canonical execution tracking
  **Rollback:** retain additive field

- **Bridge:** `legacy A2A Expert optional selection`
  **Canonical replacement:** strict canonical Expert contract
  **External owner:** A2A consumer owner
  **Known consumers:** `Unknown`
  **Status:** External Pending
  **Blocker evidence:** caller lacks `allowed_tools`/`forced_tool` contract
  **Risk:** permissive legacy caller can bypass strict selection
  **Review date:** `2026-08-01`
  **Exit condition:** consumer sends constraints and paired compatibility tests
  pass
  **Rollback:** keep Web `bot.expert_enabled=false`

- **Bridge:** `MCP stdio legacy response`
  **Canonical replacement:** canonical typed client/result projection
  **External owner:** MCP client owners
  **Known consumers:** external stdio clients unknown
  **Status:** External Pending
  **Blocker evidence:** client matrix evidence absent
  **Risk:** external parse break
  **Review date:** `2026-08-01`
  **Exit condition:** client matrix passes canonical response and rollback owner
  is named
  **Rollback:** retain stdio bridge

### Legacy A2A Expert boundary

The registered bridge is legacy A2A optional selection. Its Bot behavior is
all-schema `tool_choice=auto` with optional selection: a no-selection result
keeps the existing ChatAgent fallback. The strict `/v1/query/route` contract
now has its own opt-in chat degrade: a genuine model *decline* (no tool call)
resolves to a ChatAgent dispatch only when the caller's trusted `allowed_tools`
includes `ChatAgent`, and otherwise stays a `502`. This is distinct from the
legacy bridge, which still requires no `allowed_tools`/`forced_tool`; the two
paths remain isolated (a legacy `None` selection never relaxes the strict
route). No consumer names are asserted without evidence. The rollback is to
keep Web `bot.expert_enabled=false`.
