# Phytomni-Bot Contract Convergence Ledger

This ledger indexes reproducible Bot evidence for the six-plan contract
convergence roadmap. It distinguishes Bot-local readiness from external
acceptance. The baseline SHA was captured before this ledger commit; the final
current-SHA packet must be regenerated after the last Bot commit.

Baseline branch: `release/0.1.4`\
Baseline SHA: `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`

Allowed statuses: `Unknown`, `Needs Verification`, `Bot Ready`, `External Pending`, `Accepted`, `Blocked`, `Rejected`.

## Current Bot evidence packet

Evidence base SHA: `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`\
Capability golden SHA256: `b13f327b1dd1012ef24936cf3183bd37a19d0e1e8ec3dd7a5115352d0ea492b5`

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
`UV_CACHE_DIR=/tmp/phytomni-uv-cache make scoped` gate passed with `2173 passed`; the secret scan and static-analysis exemption reconciliation were
clean. The public-document sentinel scan returned no matches.

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

| Requirement                        | Source                              | SHA                                        | Environment | Command                    | Exit/result                | Sample                   | Owner           | Status             | Blocker                      | Rollback                    |
| ---------------------------------- | ----------------------------------- | ------------------------------------------ | ----------- | -------------------------- | -------------------------- | ------------------------ | --------------- | ------------------ | ---------------------------- | --------------------------- |
| Section 6 locale                   | Spec 6; locale Tasks 1-5            | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | locale packet              | Not implemented            | synthetic locale         | Locale plan     | Needs Verification | plan not started             | revert locale commits       |
| Section 7 capabilities/attachments | Spec 7; locale Tasks 6-9            | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | capability packet          | Legacy baseline only       | ten-agent matrix         | Locale plan     | Needs Verification | unified registry absent      | revert registry commits     |
| Section 8 response projection      | Spec 8; projection Tasks 1-2        | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | projection packet          | 235 focused; 2173 scoped   | canonical agent.run      | Projection plan | Bot Ready          | Web/Go acceptance external   | retain compatibility fields |
| Section 9 lifecycle                | Spec 9; lifecycle Tasks 1-8         | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | completion packet          | 231 passed; scoped blocked | sanitized Review run     | Lifecycle plan  | Needs Verification | full gate and matrix absent  | revert lifecycle commits    |
| Section 10 Review/Chat A2UI        | Spec 10; lifecycle Tasks 5-8        | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI packet                | 75 Task 8 tests passed     | pause golden             | Lifecycle plan  | Needs Verification | external consumer absent     | keep flags dark             |
| Section 11 artifacts/reports       | Spec 11; projection Tasks 3-8       | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | report packet              | 235 focused; 2173 scoped   | role manifest            | Projection plan | Bot Ready          | Web/Go acceptance external   | revert report commits       |
| Section 12 Expert                  | Spec 12; Expert Tasks 1-6           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | Expert packet              | Legacy route baseline      | selector request         | Expert plan     | Needs Verification | shared integration absent    | keep route dark             |
| Section 13 errors                  | Spec 13; lifecycle/Expert/Data      | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | safe error packet          | Lifecycle portion covered  | safe error body          | Lifecycle plan  | Needs Verification | later mappings absent        | revert mapping commits      |
| Section 14 DataAgent               | Spec 14; Data Tasks 1-5             | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | exact-query replay         | Not authorized             | stage event              | Data plan       | External Pending   | backend authorization absent | no behavior change          |
| Section 15 Analyst                 | Spec 15; Data Tasks 6-7             | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | correlation probe          | Not implemented            | dry-run match            | Data plan       | External Pending   | owner evidence absent        | no historical write         |
| Phase 0 evidence freeze            | Index orchestration Task 1          | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | ledger schema tests        | In progress                | this ledger              | Acceptance plan | Needs Verification | baseline commit pending      | remove ledger commit        |
| Phase 1 Lifecycle P0 exit          | Index Phase 1                       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | lifecycle packet           | 231 passed                 | focused logs             | Lifecycle plan  | Needs Verification | scoped/full/matrix absent    | revert phase commits        |
| Phase 2 Locale/Attachments exit    | Index Phase 2                       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | locale packet              | Not started                | capability golden        | Locale plan     | Needs Verification | plan not started             | revert phase commits        |
| Phase 3 Scientific Projection exit | Index Phase 3                       | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | projection packet          | 235 focused; 2173 scoped   | report golden            | Projection plan | Bot Ready          | Web/Go acceptance external   | revert phase commits        |
| Phase 4 Expert exit                | Index Phase 4                       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | Expert packet              | Not started                | route bodies             | Expert plan     | Needs Verification | phases 2-3 absent            | revert phase commits        |
| Phase 5 DataAgent exit             | Index Phase 5                       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | Data packet                | Not started                | trace evidence           | Data plan       | External Pending   | root-cause gate absent       | no behavior change          |
| Phase 6 Current-SHA Bot Ready      | Index Phase 6; acceptance Tasks 3-5 | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local/CI    | final packet               | Not started                | SHA manifest             | Acceptance plan | Needs Verification | full gate absent             | keep flags dark             |
| Phase 7 Migration Cleanup          | Index Phase 7; acceptance Task 7    | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | bridge packet              | Not eligible               | accepted-row proof       | Acceptance plan | External Pending   | Web/Go/staging absent        | bridge rollback             |
| B1                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_actions_http.py  | Covered locally            | action envelope          | Lifecycle plan  | Needs Verification | final gate absent            | revert action contract      |
| B2                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI HTTP tests            | Covered locally            | synthetic action         | Lifecycle plan  | Needs Verification | consumer absent              | revert action contract      |
| B3                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI review tests          | Covered locally            | rejected confirm         | Lifecycle plan  | Needs Verification | final evidence absent        | preserve rejection          |
| B4                                 | Spec 22; lifecycle Task 8           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | contract fixtures          | Covered locally            | form submit/cancel       | Lifecycle plan  | Needs Verification | final evidence absent        | revert form projection      |
| B5                                 | Spec 22; lifecycle Task 8           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | contract fixtures          | Covered locally            | choice submit/cancel     | Lifecycle plan  | Needs Verification | final evidence absent        | revert choice projection    |
| B6                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI terminal tests        | Covered locally            | submitted answer         | Lifecycle plan  | Needs Verification | final gate absent            | revert terminal projection  |
| B7                                 | Spec 22; lifecycle Task 8           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | Review form tests          | Covered locally            | submitted fields         | Lifecycle plan  | Needs Verification | final gate absent            | revert field projection     |
| B8                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | multi-turn tests           | Covered locally            | fresh interrupt          | Lifecycle plan  | Needs Verification | final gate absent            | revert pause projection     |
| B9                                 | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | multi-turn tests           | Covered locally            | two pause rounds         | Lifecycle plan  | Needs Verification | final gate absent            | keep N=2 bound              |
| B10                                | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_runtime.py       | Covered locally            | round-two surface        | Lifecycle plan  | Needs Verification | final gate absent            | revert loop bound           |
| B11                                | Spec 22; lifecycle Task 6           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_run_registry.py       | Covered locally            | action audit             | Lifecycle plan  | Needs Verification | final gate absent            | revert audit schema         |
| B12                                | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI HTTP tests            | Covered locally            | replay 409               | Lifecycle plan  | Needs Verification | final gate absent            | revert claim path           |
| B13                                | Spec 22; lifecycle Task 6           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_run_registry.py       | Covered locally            | atomic claim             | Lifecycle plan  | Needs Verification | final gate absent            | revert CAS transaction      |
| B14                                | Spec 22; lifecycle Task 7           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | A2UI matrix                | Covered locally            | owner/surface/checkpoint | Lifecycle plan  | Needs Verification | final gate absent            | revert validation           |
| B15                                | Spec 22; lifecycle Task 6           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | audit projection tests     | Covered locally            | safe audit columns       | Lifecycle plan  | Needs Verification | final gate absent            | revert audit exposure       |
| B16                                | Spec 22; lifecycle Task 8           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | lifecycle invariant tests  | Covered locally            | file-backed restart      | Lifecycle plan  | Needs Verification | final gate absent            | revert restart seam         |
| B17                                | Spec 22; lifecycle Task 8           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | contract fixtures          | Covered locally            | input_required body      | Lifecycle plan  | Needs Verification | final gate absent            | revert fixture              |
| B18                                | Spec 22; lifecycle limits           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_limits.py        | Pending packet             | exact/+1 budgets         | Lifecycle plan  | Needs Verification | final packet pending         | keep limits unchanged       |
| C1                                 | Spec 23; acceptance Task 5          | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local/CI    | current-SHA capture        | Not implemented            | SHA manifest             | Acceptance plan | Needs Verification | evidence script absent       | discard stale artifact      |
| C2                                 | Spec 23; locale Task 6              | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | capability golden          | Legacy baseline only       | ten-agent matrix         | Locale plan     | Needs Verification | registry absent              | revert registry             |
| C3                                 | Spec 23; locale/Expert plans        | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | streaming capability test  | Not implemented            | streaming list           | Expert plan     | Needs Verification | registry absent              | keep behavior               |
| C4                                 | Spec 23; lifecycle limits           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_limits.py        | Pending packet             | 64 KiB exact/+1          | Lifecycle plan  | Needs Verification | packet pending               | keep ingress cap            |
| C5                                 | Spec 23; lifecycle limits           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_limits.py        | Pending packet             | 1 MiB exact/+1           | Lifecycle plan  | Needs Verification | packet pending               | keep response cap           |
| C6                                 | Spec 23; lifecycle Task 5           | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | test_a2ui_review_http.py   | Covered locally            | flag-off Review          | Lifecycle plan  | Needs Verification | external absent              | keep flag dark              |
| C7                                 | Spec 23; streaming/projection       | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | streaming packet           | Legacy baseline only       | accumulated answer       | Projection plan | Needs Verification | projection absent            | revert stream projection    |
| C8                                 | Spec 23; projection plan            | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | execution packet           | 235 focused; 2173 scoped   | no raw/provider          | Projection plan | Bot Ready          | Web/Go acceptance external   | revert projection           |
| C9                                 | Spec 23; projection Task 8          | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local       | DeepGenome capability test | 235 focused; 2173 scoped   | bounded report           | Projection plan | Bot Ready          | Web/Go acceptance external   | preserve canonical result   |
| C10                                | Spec 23; acceptance Tasks 4-5       | `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3` | local/CI    | full gate/worktree         | 2173 scoped; clean tree    | clean SHA packet         | Acceptance plan | Bot Ready          | Web/Go acceptance external   | discard stale packet        |
| Bot Ready definition               | Spec 25.1; acceptance Tasks 4-5     | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local/CI    | final evidence packet      | Not ready                  | checklist                | Acceptance plan | Needs Verification | phases absent                | keep flags dark             |
| Accepted definition                | Spec 25.2; acceptance Tasks 6-8     | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | paired evidence            | Not accepted               | paired SHA               | Acceptance plan | External Pending   | Web/Go/staging absent        | no activation               |
| Data exact-query gate              | Spec 14; Data Tasks 1-5             | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | guarded replay             | Not authorized             | stage trace              | Data plan       | External Pending   | backend authorization absent | no behavior fix             |
| Analyst new-run correlation        | Spec 15; Data Task 6                | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | local       | identity tests             | Not implemented            | run/task IDs             | Data plan       | Needs Verification | plan not started             | revert correlation          |
| Analyst historical L2 boundary     | Spec 15; Data Task 7                | `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac` | external    | read-only probe            | Not authorized             | match status             | Data plan       | External Pending   | owner approval absent        | zero writes                 |

## Handoff dispositions

| Handoff                                            | Authority       | Disposition                                           | Owner plan                          | Status             | Evidence                             |
| -------------------------------------------------- | --------------- | ----------------------------------------------------- | ----------------------------------- | ------------------ | ------------------------------------ |
| `2026-07-15-a2ui-bot-contract-handoff.md`          | provenance-only | migrated into lifecycle/A2UI contract                 | Lifecycle/A2UI plan                 | Needs Verification | lifecycle commits and focused packet |
| `2026-07-18-bot-head-web-compatibility-handoff.md` | provenance-only | migrated into projection and acceptance scope         | Projection/acceptance plans         | External Pending   | paired Web/Go evidence absent        |
| `2026-07-21-real-user-feedback-bot-handoff.md`     | provenance-only | split across locale, reports, and Data/Analyst tracks | Locale, reports, Data/Analyst plans | Needs Verification | complete packet absent               |
| `2026-07-24-instant-expert-routing-bot-handoff.md` | provenance-only | retained as Expert baseline input                     | Expert plan                         | Needs Verification | route baseline only                  |
| `2026-07-24-dataagent-bot-handoff.md`              | provenance-only | retained as evidence-only Data/Analyst input          | Data/Analyst plan                   | External Pending   | replay and owner evidence absent     |

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
