# Phytomni-Bot Compatibility Register

These bridges are temporary projections, not alternate sources of truth. Each
row remains active until its owner supplies the evidence in its exit condition.
Initial review date: `2026-08-01`.

## Compatibility register

| Bridge                                 | Canonical replacement                    | External owner     | Known consumers                | Status           | Blocker evidence                                    | Risk                                                 | Review date  | Exit condition                                                        | Rollback                            |
| -------------------------------------- | ---------------------------------------- | ------------------ | ------------------------------ | ---------------- | --------------------------------------------------- | ---------------------------------------------------- | ------------ | --------------------------------------------------------------------- | ----------------------------------- |
| `run_id alias`                         | `id` as primary run identity             | Web and Go owners  | Web history and Go gateway     | External Pending | paired SHA and staging body absent                  | duplicate identity parsing                           | `2026-08-01` | Web and Go consume byte-identical `id` with matching fixture hashes   | retain alias                        |
| `formatted execution fields`           | `result.execution`                       | Web owner          | Web history/detail consumers   | External Pending | canonical consumer evidence absent                  | operational fields may be dropped                    | `2026-08-01` | Web reads tracking/warnings/tasks/artifacts from canonical projection | retain additive fields              |
| `DeepGenome formatted report metadata` | `result.execution.report` descriptor     | Web and Go owners  | DeepGenome report views        | External Pending | consumer migration and staging evidence absent      | report metadata divergence                           | `2026-08-01` | consumers read the bounded canonical report descriptor                | retain old projection               |
| `top-level degraded_tracking`          | `result.execution.tracking.degraded`     | Web and Go owners  | remote-run status consumers    | External Pending | consumer field migration absent                     | degraded state may be hidden                         | `2026-08-01` | paired consumers read canonical execution tracking                    | retain additive field               |
| `legacy A2A Expert optional selection` | strict canonical Expert contract         | A2A consumer owner | `Unknown`                      | External Pending | caller lacks `allowed_tools`/`forced_tool` contract | permissive legacy caller can bypass strict selection | `2026-08-01` | consumer sends constraints and paired compatibility tests pass        | keep Web `bot.expert_enabled=false` |
| `MCP stdio legacy response`            | canonical typed client/result projection | MCP client owners  | external stdio clients unknown | External Pending | client matrix evidence absent                       | external parse break                                 | `2026-08-01` | client matrix passes canonical response and rollback owner is named   | retain stdio bridge                 |

### Legacy A2A Expert boundary

The registered bridge is legacy A2A optional selection. Its Bot behavior is
all-schema `tool_choice=auto` with optional selection: a no-selection result
keeps the existing ChatAgent fallback. Strict-route access is forbidden for
this bridge until the consumer sends `allowed_tools` and `forced_tool`. No
consumer names are asserted without evidence. The rollback is to keep Web
`bot.expert_enabled=false`.
