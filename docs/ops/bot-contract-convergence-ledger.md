# Phytomni-Bot Contract Convergence Ledger

This ledger indexes reproducible Bot evidence for the six-plan contract
convergence roadmap. It distinguishes Bot-local readiness from external
acceptance. The baseline SHA was captured before this ledger commit; the final
current-SHA packet must be regenerated after the last Bot commit.

Baseline branch: `release/0.1.4`\
Baseline SHA: `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`

Allowed statuses: `Unknown`, `Needs Verification`, `Bot Ready`, `External Pending`, `Accepted`, `Blocked`, `Rejected`.

## Current Bot evidence packet

Evidence base SHA: `edcf25d7` (Task 3 acceptance assets; not the final
current-SHA packet)\
Capability golden SHA256: `b13f327b1dd1012ef24936cf3183bd37a19d0e1e8ec3dd7a5115352d0ea492b5`

The [Bot contract acceptance runbook](bot-contract-acceptance-runbook.md) is
the authoritative current-SHA procedure. The focused packet, full local gate,
and supported-Python matrix must be rerun after the final tracked-doc commit;
until then the final packet status is `Needs Verification`.

Focused completion command:

```bash
UV_CACHE_DIR=/tmp/phytomni-uv-cache uv run pytest \
  tests/unit/test_result_formatting_projection.py \
  tests/server/test_result_formatting.py \
  tests/server/test_result_formatting_metadata_contract.py \
  tests/server/test_scientific_execution_projection.py \
  tests/unit/test_artifact_roles.py \
  tests/unit/test_terminal_artifacts.py \
  tests/unit/test_terminal_report.py \
  tests/unit/test_terminal_answer.py \
  tests/unit/test_run_registry_reconcile.py \
  tests/agents/test_analyst_submission_helpers.py \
  tests/agents/test_deep_genome_dispatch_contract.py \
  tests/agents/test_deep_genome_report.py \
  tests/agents/test_deep_genome_report_async.py \
  tests/unit/test_deep_genome_report_snapshot.py \
  tests/server/test_agent_capabilities.py -q
```

Focused result: `235 passed in 1.43s`. The Task 9 packet also passed
`mdformat --check`, `pymarkdown scan`, and `git diff --check`. The full
`UV_CACHE_DIR=/tmp/phytomni-uv-cache make scoped` gate passed with `2202 passed`; the secret scan and static-analysis exemption reconciliation were
clean. The public-document sentinel scan returned no matches.

Expert Task 6 focused completion command:

```bash
UV_CACHE_DIR=/tmp/phytomni-uv-cache uv run pytest \
  tests/server/test_query_route.py \
  tests/server/test_expert_contract_http.py \
  tests/agents/test_expert_router.py \
  tests/unit/api/a2a/test_messages.py -q
```

Focused result: `97 passed in 7.39s`. The packet locks strict no-selection
failure, native dispatch isolation, exact tool ordering, and the legacy A2A
optional-selection boundary. External Web/Go paired acceptance and A2A
consumer constraints remain pending; rollback keeps Web
`bot.expert_enabled=false`.

Lifecycle P0 focused completion command:

```bash
UV_CACHE_DIR=/tmp/phytomni-uv-cache uv run pytest \
  tests/unit/test_lifecycle_contract.py \
  tests/unit/test_request_context.py \
  tests/unit/test_submission_outcome.py \
  tests/unit/test_run_registry.py \
  tests/agents/test_research_contracts.py \
  tests/server/test_submit_task_recording.py \
  tests/server/test_lifecycle_invariants_http.py \
  tests/server/test_api_agent_runs.py \
  tests/server/test_api_chat_streaming.py \
  tests/server/test_a2ui_review_http.py \
  tests/server/test_a2ui_actions_http.py \
  tests/server/test_a2ui_runtime.py \
  tests/server/test_a2ui_limits.py \
  tests/server/test_a2ui_contract_fixtures.py \
  tests/server/test_resume_http.py -q
```

Focused result: `239 passed in 14.43s`. The packet covers lifecycle
invariants, accepted-task degradation, durable success settlement, typed
remote outcomes, deterministic Review surfaces, persistent A2UI claims, and
classic/A2UI resume conflicts. External consumer and supported-version
acceptance remain pending; feature flags stay dark.

### Scientific report capability rows

| Bot row                         | Report states           | Artifacts           | Degraded outcomes   | Status           |
| ------------------------------- | ----------------------- | ------------------- | ------------------- | ---------------- |
| `analyst`                       | `final`                 | `true`              | `true`              | Bot Ready        |
| `research`                      | `final`                 | `true`              | `true`              | Bot Ready        |
| `design`                        | `final`                 | `true`              | `true`              | Bot Ready        |
| `network`                       | `final`                 | `true`              | `true`              | Bot Ready        |
| `deep_genome`                   | `intermediate`, `final` | `true`              | `true`              | Bot Ready        |
| Web/Go/report-history migration | external acceptance     | external acceptance | external acceptance | External Pending |

## Requirement ledger

| Requirement                        | Source                              | SHA                                        | Environment | Command                    | Exit/result                                             | Sample                                  | Owner           | Status             | Blocker                                                        | Rollback                    |
| ---------------------------------- | ----------------------------------- | ------------------------------------------ | ----------- | -------------------------- | ------------------------------------------------------- | --------------------------------------- | --------------- | ------------------ | -------------------------------------------------------------- | --------------------------- |
| Section 6 locale                   | Spec 6; locale Tasks 1-5            | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | locale packet              | 216 focused; 2202 scoped                                | synthetic locale                        | Locale plan     | Bot Ready          | Web/Go consumer acceptance external                            | revert locale commits       |
| Section 7 capabilities/attachments | Spec 7; locale Tasks 6-9            | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | capability packet          | 216 focused; 2202 scoped                                | ten-agent matrix                        | Locale plan     | Bot Ready          | Web/Go consumer acceptance external                            | revert registry commits     |
| Section 8 response projection      | Spec 8; projection Tasks 1-2        | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | projection packet          | 235 focused; 2173 scoped                                | canonical agent.run                     | Projection plan | Bot Ready          | Web/Go acceptance external                                     | retain compatibility fields |
| Section 9 lifecycle                | Spec 9; lifecycle Tasks 1-8         | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | completion packet          | 239 focused; 2202 scoped                                | sanitized Review run                    | Lifecycle plan  | Bot Ready          | Web/Go and supported-version acceptance external               | revert lifecycle commits    |
| Section 10 Review/Chat A2UI        | Spec 10; lifecycle Tasks 5-8        | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI packet                | 239 focused; 2202 scoped                                | pause golden                            | Lifecycle plan  | Bot Ready          | external A2UI consumer acceptance                              | keep flags dark             |
| Section 11 artifacts/reports       | Spec 11; projection Tasks 3-8       | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | report packet              | 235 focused; 2173 scoped                                | role manifest                           | Projection plan | Bot Ready          | Web/Go acceptance external                                     | revert report commits       |
| Section 12 Expert                  | Spec 12; Expert Tasks 1-6           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | Expert packet              | 97 focused; 2202 scoped                                 | strict selector + A2A boundary          | Expert plan     | Bot Ready          | Web/Go paired acceptance and A2A consumer constraints external | keep route dark             |
| Section 13 errors                  | Spec 13; lifecycle/Expert/Data      | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | safe error packet          | Lifecycle portion covered                               | safe error body                         | Lifecycle plan  | Needs Verification | later mappings absent                                          | revert mapping commits      |
| Section 14 DataAgent               | Spec 14; Data Tasks 1-5             | `c1ff3c1cc7c71ad3792a4eb4724c05cb9d5b3463` | local/ext   | incident ledger/replay     | facts frozen; replay unauthorized                       | stage event                             | Data plan       | External Pending   | backend authorization absent                                   | no behavior change          |
| Section 15 Analyst                 | Spec 15; Data Tasks 6-7             | `7556596188c564fef0eb41cc3f9f7d22b06f5064` | local/ext   | correlation probe          | 107 focused; 2202 scoped                                | run/task IDs                            | Data plan       | External Pending   | historical L2 external                                         | no historical write         |
| Phase 0 evidence freeze            | Index orchestration Task 1          | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | ledger schema tests        | In progress                                             | this ledger                             | Acceptance plan | Needs Verification | baseline commit pending                                        | remove ledger commit        |
| Phase 1 Lifecycle P0 exit          | Index Phase 1                       | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | lifecycle packet           | 239 focused; 2202 scoped                                | focused logs                            | Lifecycle plan  | Bot Ready          | Web/Go and supported-version acceptance external               | revert phase commits        |
| Phase 2 Locale/Attachments exit    | Index Phase 2                       | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | locale packet              | 216 focused; 2202 scoped                                | capability golden                       | Locale plan     | Bot Ready          | Web/Go consumer acceptance external                            | revert phase commits        |
| Phase 3 Scientific Projection exit | Index Phase 3                       | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | projection packet          | 235 focused; 2173 scoped                                | report golden                           | Projection plan | Bot Ready          | Web/Go acceptance external                                     | revert phase commits        |
| Phase 4 Expert exit                | Index Phase 4                       | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | Expert packet              | 97 focused; 2202 scoped                                 | strict route and compatibility register | Expert plan     | Bot Ready          | Web/Go paired acceptance; legacy A2A constraints external      | keep route dark             |
| Phase 5 DataAgent exit             | Index Phase 5                       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | Data packet                | Not started                                             | trace evidence                          | Data plan       | External Pending   | root-cause gate absent                                         | no behavior change          |
| Phase 6 Current-SHA Bot Ready      | Index Phase 6; acceptance Tasks 3-5 | `edcf25d7`                                 | local/CI    | final packet               | Task 3 assets; final focused/full/matrix packet pending | SHA manifest                            | Acceptance plan | Needs Verification | final post-doc SHA and matrix not captured                     | keep flags dark             |
| Phase 7 Migration Cleanup          | Index Phase 7; acceptance Task 7    | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | bridge packet              | Not eligible                                            | accepted-row proof                      | Acceptance plan | External Pending   | Web/Go/staging absent                                          | bridge rollback             |
| B1                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_actions_http.py  | 239 focused; 2202 scoped                                | action envelope                         | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert action contract      |
| B2                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI HTTP tests            | 239 focused; 2202 scoped                                | synthetic action                        | Lifecycle plan  | Bot Ready          | external consumer absent                                       | revert action contract      |
| B3                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI review tests          | 239 focused; 2202 scoped                                | rejected confirm                        | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | preserve rejection          |
| B4                                 | Spec 22; lifecycle Task 8           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | contract fixtures          | 239 focused; 2202 scoped                                | form submit/cancel                      | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert form projection      |
| B5                                 | Spec 22; lifecycle Task 8           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | contract fixtures          | 239 focused; 2202 scoped                                | choice submit/cancel                    | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert choice projection    |
| B6                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI terminal tests        | 239 focused; 2202 scoped                                | submitted answer                        | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert terminal projection  |
| B7                                 | Spec 22; lifecycle Task 8           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | Review form tests          | 239 focused; 2202 scoped                                | submitted fields                        | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert field projection     |
| B8                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | multi-turn tests           | 239 focused; 2202 scoped                                | fresh interrupt                         | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert pause projection     |
| B9                                 | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | multi-turn tests           | 239 focused; 2202 scoped                                | two pause rounds                        | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | keep N=2 bound              |
| B10                                | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_runtime.py       | 239 focused; 2202 scoped                                | round-two surface                       | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert loop bound           |
| B11                                | Spec 22; lifecycle Task 6           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_run_registry.py       | 239 focused; 2202 scoped                                | action audit                            | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert audit schema         |
| B12                                | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI HTTP tests            | 239 focused; 2202 scoped                                | replay 409                              | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert claim path           |
| B13                                | Spec 22; lifecycle Task 6           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_run_registry.py       | 239 focused; 2202 scoped                                | atomic claim                            | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert CAS transaction      |
| B14                                | Spec 22; lifecycle Task 7           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | A2UI matrix                | 239 focused; 2202 scoped                                | owner/surface/checkpoint                | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert validation           |
| B15                                | Spec 22; lifecycle Task 6           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | audit projection tests     | 239 focused; 2202 scoped                                | safe audit columns                      | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert audit exposure       |
| B16                                | Spec 22; lifecycle Task 8           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | lifecycle invariant tests  | 239 focused; 2202 scoped                                | file-backed restart                     | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert restart seam         |
| B17                                | Spec 22; lifecycle Task 8           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | contract fixtures          | 239 focused; 2202 scoped                                | input_required body                     | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | revert fixture              |
| B18                                | Spec 22; lifecycle limits           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_limits.py        | 239 focused; 2202 scoped                                | exact/+1 budgets                        | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | keep limits unchanged       |
| C1                                 | Spec 23; acceptance Task 5          | `edcf25d7`                                 | local/CI    | current-SHA capture        | script implemented; final packet pending                | SHA manifest                            | Acceptance plan | Needs Verification | final post-doc SHA and matrix not captured                     | discard stale artifact      |
| C2                                 | Spec 23; locale Task 6              | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | capability golden          | 216 focused; 2202 scoped                                | ten-agent matrix                        | Locale plan     | Bot Ready          | Web/Go consumer acceptance external                            | revert registry             |
| C3                                 | Spec 23; locale/Expert plans        | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | streaming capability test  | Not implemented                                         | streaming list                          | Expert plan     | Needs Verification | registry absent                                                | keep behavior               |
| C4                                 | Spec 23; lifecycle limits           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_limits.py        | 239 focused; 2202 scoped                                | 64 KiB exact/+1                         | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | keep ingress cap            |
| C5                                 | Spec 23; lifecycle limits           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_limits.py        | 239 focused; 2202 scoped                                | 1 MiB exact/+1                          | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | keep response cap           |
| C6                                 | Spec 23; lifecycle Task 5           | `48e4da0f9a6feaa190b5e267b58e6edf3667e743` | local       | test_a2ui_review_http.py   | 239 focused; 2202 scoped                                | flag-off Review                         | Lifecycle plan  | Bot Ready          | external consumer acceptance                                   | keep flag dark              |
| C7                                 | Spec 23; streaming/projection       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | streaming packet           | Legacy baseline only                                    | accumulated answer                      | Projection plan | Needs Verification | projection absent                                              | revert stream projection    |
| C8                                 | Spec 23; projection plan            | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | execution packet           | 235 focused; 2173 scoped                                | no raw/provider                         | Projection plan | Bot Ready          | Web/Go acceptance external                                     | revert projection           |
| C9                                 | Spec 23; projection Task 8          | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | DeepGenome capability test | 235 focused; 2173 scoped                                | bounded report                          | Projection plan | Bot Ready          | Web/Go acceptance external                                     | preserve canonical result   |
| C10                                | Spec 23; acceptance Tasks 4-5       | `edcf25d7`                                 | local/CI    | full gate/worktree         | scoped gate passed; final full gate pending             | clean SHA packet                        | Acceptance plan | Needs Verification | final post-doc SHA and full gate not captured                  | discard stale packet        |
| Bot Ready definition               | Spec 25.1; acceptance Tasks 4-5     | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local/CI    | final evidence packet      | Not ready                                               | checklist                               | Acceptance plan | Needs Verification | phases absent                                                  | keep flags dark             |
| Accepted definition                | Spec 25.2; acceptance Tasks 6-8     | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | paired evidence            | Not accepted                                            | paired SHA                              | Acceptance plan | External Pending   | Web/Go/staging absent                                          | no activation               |
| Data exact-query gate              | Spec 14; Data Tasks 1-5             | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | guarded replay             | Not authorized                                          | stage trace                             | Data plan       | External Pending   | backend authorization absent                                   | no behavior fix             |
| Analyst new-run correlation        | Spec 15; Data Task 6                | `7556596188c564fef0eb41cc3f9f7d22b06f5064` | local       | identity tests             | 107 focused; 2174 scoped                                | run/task IDs                            | Data plan       | Bot Ready          | historical L2 external                                         | revert correlation          |
| Analyst historical L2 boundary     | Spec 15; Data Task 7                | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | read-only probe            | Not authorized                                          | match status                            | Data plan       | External Pending   | owner approval absent                                          | zero writes                 |

## Handoff dispositions

| Handoff                                            | Authority       | Disposition                                            | Owner plan                          | Status             | Evidence                                                         |
| -------------------------------------------------- | --------------- | ------------------------------------------------------ | ----------------------------------- | ------------------ | ---------------------------------------------------------------- |
| `2026-07-15-a2ui-bot-contract-handoff.md`          | provenance-only | migrated into lifecycle/A2UI contract                  | Lifecycle/A2UI plan                 | Needs Verification | lifecycle commits and focused packet                             |
| `2026-07-18-bot-head-web-compatibility-handoff.md` | provenance-only | migrated into projection and acceptance scope          | Projection/acceptance plans         | External Pending   | paired Web/Go evidence absent                                    |
| `2026-07-21-real-user-feedback-bot-handoff.md`     | provenance-only | split across locale, reports, and Data/Analyst tracks  | Locale, reports, Data/Analyst plans | Needs Verification | complete packet absent                                           |
| `2026-07-24-instant-expert-routing-bot-handoff.md` | provenance-only | migrated into strict selector/lifecycle/error contract | Expert plan                         | Bot Ready          | `48e4da0f`; 97 focused; 2202 scoped; external activation pending |
| `2026-07-24-dataagent-bot-handoff.md`              | provenance-only | retained as evidence-only Data/Analyst input           | Data/Analyst plan                   | External Pending   | incident facts frozen; replay and owner evidence absent          |

## Migration policy

- Invalid old behavior is repaired immediately, not preserved as a
  compatibility excuse.
- Bot internals and the Python client migrate in-tree.
- Web and Go receive compatibility projections until paired acceptance
  evidence exists.
- MCP stdio and A2A legacy behavior remain only with a blocker, owner, review
  date, and exit condition in the compatibility register.
- Historical Analyst writes are a separate L2 operation and are not
  authorized by this application roadmap.
