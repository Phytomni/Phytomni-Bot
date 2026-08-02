# Phytomni-Bot Contract Convergence Ledger

This ledger indexes reproducible Bot evidence for the six-plan contract
convergence roadmap. It distinguishes Bot-local readiness from external
acceptance. The baseline SHA was captured before this ledger commit; the final
current-SHA packet must be regenerated after the last Bot commit.

Baseline branch: `release/0.1.4`\
Baseline SHA: `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`

Allowed statuses: `Unknown`, `Needs Verification`, `Bot Ready`,
`External Pending`, `Accepted`, `Blocked`, `Rejected`.

## Current-SHA local reconciliation

The following evidence was captured on the clean source candidate immediately
before this documentation-only reconciliation commit:

- Branch: `release/0.1.4`.
- Source candidate: `cdb3a29b77bdfa7f75f75d32339d6cff3fcd9803`.
- Tracked worktree: clean; the branch was ahead of its remote by 16 commits;
  no push, merge, rebase, or production action was performed.
- Acceptance focused packet: `380 passed in 19.95s`.
- Convergence packet: `436 passed, 8 deselected` in 18.96s.
- Scoped gate: `254 passed, 8 deselected`, with exact cross-file Pylint
  `0 records`.
- Full local gate: `4133 passed, 9 deselected`, total coverage `87.78%`.
- Conversation-context fixture SHA-256:
  `8d432c8ebd6c4912667566b211177c4f5ee2f19bf4f8df935861ccf016a89633`.
- The Bot-local implementation is ready for review. Python 3.13/3.14 matrix,
  Web/Go forwarding and settlement, browser, real backend/operator, staging,
  production, CI, and feature activation remain `Needs Verification` or
  `External Pending` according to the relevant owner boundary.

This section is a pre-commit evidence snapshot. The documentation commit that
follows changes `HEAD`; any final acceptance packet must bind its own logs to
that post-commit SHA. It must not reuse this snapshot as final current-SHA
proof.

## Historical packet snapshot

Evidence base SHA: `edcf25d7` (Task 3 acceptance assets; not the final
current-SHA packet)\
Capability golden SHA256:
`b13f327b1dd1012ef24936cf3183bd37a19d0e1e8ec3dd7a5115352d0ea492b5`

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
`UV_CACHE_DIR=/tmp/phytomni-uv-cache make scoped` gate passed with
`2202 passed`; the secret scan and static-analysis exemption reconciliation were
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

- **Bot row:** `analyst`
  **Report states:** `final`
  **Artifacts:** `true`
  **Degraded outcomes:** `true`
  **Status:** Bot Ready

- **Bot row:** `research`
  **Report states:** `final`
  **Artifacts:** `true`
  **Degraded outcomes:** `true`
  **Status:** Bot Ready

- **Bot row:** `design`
  **Report states:** `final`
  **Artifacts:** `true`
  **Degraded outcomes:** `true`
  **Status:** Bot Ready

- **Bot row:** `network`
  **Report states:** `final`
  **Artifacts:** `true`
  **Degraded outcomes:** `true`
  **Status:** Bot Ready

- **Bot row:** `deep_genome`
  **Report states:** `intermediate`, `final`
  **Artifacts:** `true`
  **Degraded outcomes:** `true`
  **Status:** Bot Ready

- **Bot row:** Web/Go/report-history migration
  **Report states:** external acceptance
  **Artifacts:** external acceptance
  **Degraded outcomes:** external acceptance
  **Status:** External Pending

## Requirement ledger

- **Requirement:** Section 6 locale
  **Source:** Spec 6; locale Tasks 1-5
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** locale packet
  **Exit/result:** 216 focused; 2202 scoped
  **Sample:** synthetic locale
  **Owner:** Locale plan
  **Status:** Bot Ready
  **Blocker:** Web/Go consumer acceptance external
  **Rollback:** revert locale commits

- **Requirement:** Section 7 capabilities/attachments
  **Source:** Spec 7; locale Tasks 6-9
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** capability packet
  **Exit/result:** 216 focused; 2202 scoped
  **Sample:** ten-agent matrix
  **Owner:** Locale plan
  **Status:** Bot Ready
  **Blocker:** Web/Go consumer acceptance external
  **Rollback:** revert registry commits

- **Requirement:** Section 8 response projection
  **Source:** Spec 8; projection Tasks 1-2
  **SHA:** `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`
  **Environment:** local
  **Command:** projection packet
  **Exit/result:** 235 focused; 2173 scoped
  **Sample:** canonical agent.run
  **Owner:** Projection plan
  **Status:** Bot Ready
  **Blocker:** Web/Go acceptance external
  **Rollback:** retain compatibility fields

- **Requirement:** Section 9 lifecycle
  **Source:** Spec 9; lifecycle Tasks 1-8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** completion packet
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** sanitized Review run
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** Web/Go and supported-version acceptance external
  **Rollback:** revert lifecycle commits

- **Requirement:** Section 10 Review/Chat A2UI
  **Source:** Spec 10; lifecycle Tasks 5-8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI packet
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** pause golden
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external A2UI consumer acceptance
  **Rollback:** keep flags dark

- **Requirement:** Section 11 artifacts/reports
  **Source:** Spec 11; projection Tasks 3-8
  **SHA:** `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`
  **Environment:** local
  **Command:** report packet
  **Exit/result:** 235 focused; 2173 scoped
  **Sample:** role manifest
  **Owner:** Projection plan
  **Status:** Bot Ready
  **Blocker:** Web/Go acceptance external
  **Rollback:** revert report commits

- **Requirement:** Section 12 Expert
  **Source:** Spec 12; Expert Tasks 1-6
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** Expert packet
  **Exit/result:** 97 focused; 2202 scoped
  **Sample:** strict selector + A2A boundary
  **Owner:** Expert plan
  **Status:** Bot Ready
  **Blocker:** Web/Go paired acceptance and A2A consumer constraints external
  **Rollback:** keep route dark

- **Requirement:** Section 13 errors
  **Source:** Spec 13; lifecycle/Expert/Data
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local
  **Command:** safe error packet
  **Exit/result:** lifecycle, Expert, and locale mappings covered; final packet
  pending
  **Sample:** safe error body
  **Owner:** Lifecycle plan
  **Status:** Needs Verification
  **Blocker:** current-SHA packet not regenerated
  **Rollback:** revert mapping commits

- **Requirement:** Section 14 DataAgent
  **Source:** Spec 14; Data Tasks 1-5
  **SHA:** `c1ff3c1cc7c71ad3792a4eb4724c05cb9d5b3463`
  **Environment:** local/ext
  **Command:** incident ledger/replay
  **Exit/result:** facts frozen; authorized replay completed; provider contract
  pending
  **Sample:** stage event
  **Owner:** Data plan
  **Status:** External Pending
  **Blocker:** provider interpretation absent
  **Rollback:** no behavior change

- **Requirement:** Section 15 Analyst
  **Source:** Spec 15; Data Tasks 6-7
  **SHA:** `7556596188c564fef0eb41cc3f9f7d22b06f5064`
  **Environment:** local/ext
  **Command:** correlation probe
  **Exit/result:** new-run correlation covered; historical L2 remains pending
  **Sample:** run/task IDs
  **Owner:** Data plan
  **Status:** External Pending
  **Blocker:** historical L2 external
  **Rollback:** no historical write

- **Requirement:** Phase 0 evidence freeze
  **Source:** Index orchestration Task 1
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local
  **Command:** ledger schema tests
  **Exit/result:** In progress
  **Sample:** this ledger
  **Owner:** Acceptance plan
  **Status:** Needs Verification
  **Blocker:** baseline commit pending
  **Rollback:** remove ledger commit

- **Requirement:** Phase 1 Lifecycle P0 exit
  **Source:** Index Phase 1
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** lifecycle packet
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** focused logs
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** Web/Go and supported-version acceptance external
  **Rollback:** revert phase commits

- **Requirement:** Phase 2 Locale/Attachments exit
  **Source:** Index Phase 2
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** locale packet
  **Exit/result:** 216 focused; 2202 scoped
  **Sample:** capability golden
  **Owner:** Locale plan
  **Status:** Bot Ready
  **Blocker:** Web/Go consumer acceptance external
  **Rollback:** revert phase commits

- **Requirement:** Phase 3 Scientific Projection exit
  **Source:** Index Phase 3
  **SHA:** `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`
  **Environment:** local
  **Command:** projection packet
  **Exit/result:** 235 focused; 2173 scoped
  **Sample:** report golden
  **Owner:** Projection plan
  **Status:** Bot Ready
  **Blocker:** Web/Go acceptance external
  **Rollback:** revert phase commits

- **Requirement:** Phase 4 Expert exit
  **Source:** Index Phase 4
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** Expert packet
  **Exit/result:** 97 focused; 2202 scoped
  **Sample:** strict route and compatibility register
  **Owner:** Expert plan
  **Status:** Bot Ready
  **Blocker:** Web/Go paired acceptance; legacy A2A constraints external
  **Rollback:** keep route dark

- **Requirement:** Phase 5 DataAgent exit
  **Source:** Index Phase 5
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** external
  **Command:** Data packet
  **Exit/result:** Not started
  **Sample:** trace evidence
  **Owner:** Data plan
  **Status:** External Pending
  **Blocker:** root-cause gate absent
  **Rollback:** no behavior change

- **Requirement:** Phase 6 Current-SHA Bot Ready
  **Source:** Index Phase 6; acceptance Tasks 3-5
  **SHA:** `edcf25d7`
  **Environment:** local/CI
  **Command:** final packet
  **Exit/result:** Task 3 assets; final focused/full/matrix packet pending
  **Sample:** SHA manifest
  **Owner:** Acceptance plan
  **Status:** Needs Verification
  **Blocker:** final post-doc SHA and matrix not captured
  **Rollback:** keep flags dark

- **Requirement:** Phase 7 Migration Cleanup
  **Source:** Index Phase 7; acceptance Task 7
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** external
  **Command:** bridge packet
  **Exit/result:** Not eligible
  **Sample:** accepted-row proof
  **Owner:** Acceptance plan
  **Status:** External Pending
  **Blocker:** Web/Go/staging absent
  **Rollback:** bridge rollback

- **Requirement:** B1
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_actions_http.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** action envelope
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert action contract

- **Requirement:** B2
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI HTTP tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** synthetic action
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer absent
  **Rollback:** revert action contract

- **Requirement:** B3
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI review tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** rejected confirm
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** preserve rejection

- **Requirement:** B4
  **Source:** Spec 22; lifecycle Task 8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** contract fixtures
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** form submit/cancel
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert form projection

- **Requirement:** B5
  **Source:** Spec 22; lifecycle Task 8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** contract fixtures
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** choice submit/cancel
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert choice projection

- **Requirement:** B6
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI terminal tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** submitted answer
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert terminal projection

- **Requirement:** B7
  **Source:** Spec 22; lifecycle Task 8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** Review form tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** submitted fields
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert field projection

- **Requirement:** B8
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** multi-turn tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** fresh interrupt
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert pause projection

- **Requirement:** B9
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** multi-turn tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** two pause rounds
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** keep N=2 bound

- **Requirement:** B10
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_runtime.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** round-two surface
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert loop bound

- **Requirement:** B11
  **Source:** Spec 22; lifecycle Task 6
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_run_registry.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** action audit
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert audit schema

- **Requirement:** B12
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI HTTP tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** replay 409
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert claim path

- **Requirement:** B13
  **Source:** Spec 22; lifecycle Task 6
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_run_registry.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** atomic claim
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert CAS transaction

- **Requirement:** B14
  **Source:** Spec 22; lifecycle Task 7
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** A2UI matrix
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** owner/surface/checkpoint
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert validation

- **Requirement:** B15
  **Source:** Spec 22; lifecycle Task 6
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** audit projection tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** safe audit columns
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert audit exposure

- **Requirement:** B16
  **Source:** Spec 22; lifecycle Task 8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** lifecycle invariant tests
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** file-backed restart
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert restart seam

- **Requirement:** B17
  **Source:** Spec 22; lifecycle Task 8
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** contract fixtures
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** input_required body
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** revert fixture

- **Requirement:** B18
  **Source:** Spec 22; lifecycle limits
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_limits.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** exact/+1 budgets
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** keep limits unchanged

- **Requirement:** C1
  **Source:** Spec 23; acceptance Task 5
  **SHA:** `edcf25d7`
  **Environment:** local/CI
  **Command:** current-SHA capture
  **Exit/result:** script implemented; final packet pending
  **Sample:** SHA manifest
  **Owner:** Acceptance plan
  **Status:** Needs Verification
  **Blocker:** final post-doc SHA and matrix not captured
  **Rollback:** discard stale artifact

- **Requirement:** C2
  **Source:** Spec 23; locale Task 6
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** capability golden
  **Exit/result:** 216 focused; 2202 scoped
  **Sample:** ten-agent matrix
  **Owner:** Locale plan
  **Status:** Bot Ready
  **Blocker:** Web/Go consumer acceptance external
  **Rollback:** revert registry

- **Requirement:** C3
  **Source:** Spec 23; locale/Expert plans
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local
  **Command:** streaming capability test
  **Exit/result:** source gate exists; focused regression added; final packet
  pending
  **Sample:** streaming list
  **Owner:** Expert plan
  **Status:** Needs Verification
  **Blocker:** current-SHA packet not regenerated
  **Rollback:** keep behavior

- **Requirement:** C4
  **Source:** Spec 23; lifecycle limits
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_limits.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** 64 KiB exact/+1
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** keep ingress cap

- **Requirement:** C5
  **Source:** Spec 23; lifecycle limits
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_limits.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** 1 MiB exact/+1
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** keep response cap

- **Requirement:** C6
  **Source:** Spec 23; lifecycle Task 5
  **SHA:** `48e4da0f9a6feaa190b5e267b58e6edf3667e743`
  **Environment:** local
  **Command:** test_a2ui_review_http.py
  **Exit/result:** 239 focused; 2202 scoped
  **Sample:** flag-off Review
  **Owner:** Lifecycle plan
  **Status:** Bot Ready
  **Blocker:** external consumer acceptance
  **Rollback:** keep flag dark

- **Requirement:** C7
  **Source:** Spec 23; streaming/projection
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local
  **Command:** streaming packet
  **Exit/result:** accumulated answer persistence and HTTP history tests
  present; final packet pending
  **Sample:** accumulated answer
  **Owner:** Projection plan
  **Status:** Needs Verification
  **Blocker:** current-SHA packet not regenerated
  **Rollback:** revert stream projection

- **Requirement:** C8
  **Source:** Spec 23; projection plan
  **SHA:** `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`
  **Environment:** local
  **Command:** execution packet
  **Exit/result:** 235 focused; 2173 scoped
  **Sample:** no raw/provider
  **Owner:** Projection plan
  **Status:** Bot Ready
  **Blocker:** Web/Go acceptance external
  **Rollback:** revert projection

- **Requirement:** C9
  **Source:** Spec 23; projection Task 8
  **SHA:** `b23d5dba15e1108c5c431f4e66a7f0a2b02ebbf3`
  **Environment:** local
  **Command:** DeepGenome capability test
  **Exit/result:** 235 focused; 2173 scoped
  **Sample:** bounded report
  **Owner:** Projection plan
  **Status:** Bot Ready
  **Blocker:** Web/Go acceptance external
  **Rollback:** preserve canonical result

- **Requirement:** C10
  **Source:** Spec 23; acceptance Tasks 4-5
  **SHA:** `edcf25d7`
  **Environment:** local/CI
  **Command:** full gate/worktree
  **Exit/result:** scoped gate passed; final full gate pending
  **Sample:** clean SHA packet
  **Owner:** Acceptance plan
  **Status:** Needs Verification
  **Blocker:** final post-doc SHA and full gate not captured
  **Rollback:** discard stale packet

- **Requirement:** Bot Ready definition
  **Source:** Spec 25.1; acceptance Tasks 4-5
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local/CI
  **Command:** final evidence packet
  **Exit/result:** Not ready
  **Sample:** checklist
  **Owner:** Acceptance plan
  **Status:** Needs Verification
  **Blocker:** phases absent
  **Rollback:** keep flags dark

- **Requirement:** Accepted definition
  **Source:** Spec 25.2; acceptance Tasks 6-8
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** external
  **Command:** paired evidence
  **Exit/result:** Not accepted
  **Sample:** paired SHA
  **Owner:** Acceptance plan
  **Status:** External Pending
  **Blocker:** Web/Go/staging absent
  **Rollback:** no activation

- **Requirement:** Data exact-query gate
  **Source:** Spec 14; Data Tasks 1-5
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** local/ext
  **Command:** guarded replay
  **Exit/result:** authorized replay completed; provider contract unresolved
  **Sample:** stage trace
  **Owner:** Data plan
  **Status:** External Pending
  **Blocker:** provider owner interpretation absent
  **Rollback:** no behavior fix

- **Requirement:** Analyst new-run correlation
  **Source:** Spec 15; Data Task 6
  **SHA:** `7556596188c564fef0eb41cc3f9f7d22b06f5064`
  **Environment:** local
  **Command:** identity tests
  **Exit/result:** 107 focused; 2174 scoped
  **Sample:** run/task IDs
  **Owner:** Data plan
  **Status:** Bot Ready
  **Blocker:** historical L2 external
  **Rollback:** revert correlation

- **Requirement:** Analyst historical L2 boundary
  **Source:** Spec 15; Data Task 7
  **SHA:** `1a44d591d5eb5cfabfb16c74b1eda41d6c527dac`
  **Environment:** external
  **Command:** read-only probe
  **Exit/result:** read-only packet implemented; execution still not authorized
  **Sample:** match status
  **Owner:** Data plan
  **Status:** External Pending
  **Blocker:** owner approval absent
  **Rollback:** zero writes

## Local reconciliation note

The current source contains the stream capability gate, accumulated HTTP
stream-answer persistence, analyst-class report assembly, native agent-run
conversation context, curated gene-example OBS reads, and resumable upload
runtime corrections. Focused/scoped/full local evidence is green for the
source candidate above. The final packet and supported-version matrix remain
separate acceptance evidence; no missing external evidence is upgraded to
`Accepted` here.

## 2026-08-02 five-item Bot evidence refresh

This is a Bot-local evidence inventory. It does not replace paired Web/Go,
provider, OBS, browser, staging, or historical-operator acceptance. Each row
separates the local result from the remaining external boundary:

- **Analyst terminal report:** `analyst_terminal_succeeded.json` and
  `test_analyst_terminal_golden_pins_local_report_projection` prove the
  synthetic final answer, report state, scientific artifact roles, and output
  directory projection. Remote completion, OBS artifacts, Web correlation, and
  historical repair remain `External Pending`.

- **DeepGenome RC-WEB:** the `RC-WEB local evidence map` in
  `docs/contracts/deep-genome/README.md` links RC-WEB-001 through RC-WEB-005
  to bounded fixtures and tests for submit, revision, failure, artifacts, and
  timeout. Live remote, OBS, paired consumer, browser, and staging evidence
  remain `External Pending`.

- **Review timeout/provider seam:** the Review timeout tests cover the HTTP,
  Agent, direct-provider, and relay-provider paths without adding an outer
  wait or changing the configured override contract. Real provider behavior,
  OBS, browser, and real-user acceptance remain `External Pending`.

- **DataAgent guarded replay:** `dataagent_incident_replay_guarded.json` and
  `test_dataagent_golden_pins_guarded_replay_boundary` pin the exact-query
  identity hash, six safe stages, and result metrics. Provider interpretation,
  root-cause confirmation, and historical Analyst correlation remain
  `External Pending`; no behavior change is authorized.

- **Expert local edges:** `expert_local_edge_contract.json` and the existing
  strict-router/native-parity packet prove forced allowlist routing, no-dispatch
  failure handling, and the dark activation boundary. Autonomous provider
  selection, paired Web/Go acceptance, and legacy A2A consumer evidence remain
  `External Pending`; Web `bot.expert_enabled` stays `false`.

## Handoff dispositions

- **Handoff:** `2026-07-15-a2ui-bot-contract-handoff.md`
  **Authority:** provenance-only
  **Disposition:** migrated into lifecycle/A2UI contract
  **Owner plan:** Lifecycle/A2UI plan
  **Status:** Needs Verification
  **Evidence:** lifecycle commits and focused packet

- **Handoff:** `2026-07-18-bot-head-web-compatibility-handoff.md`
  **Authority:** provenance-only
  **Disposition:** migrated into projection and acceptance scope
  **Owner plan:** Projection/acceptance plans
  **Status:** External Pending
  **Evidence:** paired Web/Go evidence absent

- **Handoff:** `2026-07-21-real-user-feedback-bot-handoff.md`
  **Authority:** provenance-only
  **Disposition:** split across locale, reports, and Data/Analyst tracks
  **Owner plan:** Locale, reports, Data/Analyst plans
  **Status:** Needs Verification
  **Evidence:** complete packet absent

- **Handoff:** `2026-07-24-instant-expert-routing-bot-handoff.md`
  **Authority:** provenance-only
  **Disposition:** migrated into strict selector/lifecycle/error contract
  **Owner plan:** Expert plan
  **Status:** Bot Ready
  **Evidence:** `48e4da0f`; 97 focused; 2202 scoped; external activation pending

- **Handoff:** `2026-07-24-dataagent-bot-handoff.md`
  **Authority:** provenance-only
  **Disposition:** retained as evidence-only Data/Analyst input
  **Owner plan:** Data/Analyst plan
  **Status:** External Pending
  **Evidence:** incident facts frozen; replay and owner evidence absent

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
