# Historical Gate Audit — 2026-07-14

This record separates reproducible gate evidence from the ignored execution
ledger. A checked plan entry or a commit message is not treated as proof of a
historical full-gate run.

## Method and status vocabulary

- Commit identity was rebuilt from `git log`, the current branch, and
  `git show --stat` for every delivery commit.
- Code and test scope below names the files changed by the commit and the
  focused test family inspected for that scope.
- `Needs Verification` means that the plan recorded a result, but no raw
  full-gate output or versioned digest for that exact commit is available to
  replay independently.
- `Known incomplete` means the plan explicitly recorded that the required
  post-change full gate was not run.
- `Current-tree evidence` means the result proves only the named current HEAD,
  not every earlier commit.

## Reproducible current evidence

- Before the CI-alignment commit, `8c31573` passed the authoritative local gate:
  `2488 passed, 1 deselected`, 87.96% coverage, and 110 agent module floors.
- The CI-equivalent Pylint invocation now passes with
  `--disable=R0801,R0903`; the independent baseline check reports
  `R0801=124, R0903=28` at baseline.
- Commit `237e440` passed `make scoped`: secret scan, Python tooling, workflow
  checks, Markdown checks, and 467 focused/regression tests.
- A full gate after `237e440` remains a final release-evidence task; it is not
  backdated to any historical commit.
- The remote release branch later advanced by one config-only commit,
  `f2da19e` (`Update promoter design species configuration`). The local branch
  was rebased onto that commit without conflicts; the resulting tree is clean
  and the final pushed baseline is `8806f1d`.
- The SSH transport is now reachable through the authorized local proxy route:
  key authentication and `git ls-remote` both succeed. A non-writing push
  probe passed the 503-item scoped gate before the rebase, and the final
  post-rebase `make push` completed successfully at `8806f1d`.

## Phase 0 — version, dependency, and CI calibration

- C0.1 `9e43885`: progress-event contract and docs →
  `tests/server/test_progress_events.py`; `Needs Verification`.
- C0.2 `4ba34ed`: LangGraph floor/API contract →
  `tests/unit/test_langgraph_api_contract.py` and stream/resume tests;
  `Needs Verification`.
- C0.3 `c8350a2`: dependency-floor workflow →
  `tests/unit/test_pytest_layers.py`; superseded by the clarified contract in
  `d025005`; historical full-gate evidence remains `Needs Verification`.
- C0.4 `8fd0918`: centralized runtime version → version contract tests;
  `Needs Verification`.
- C0.5 `a05ad8a`: capability-boundary docs → docs consistency tests;
  `Needs Verification`.

## Phase 1 — A2A server core

- C1.1 `cb51ec0`: SDK dependency/protobuf contract → SDK contract tests;
  `Needs Verification`.
- C1.2 `2be0d80`: A2A flags and public URL validation → config tests;
  `Needs Verification`.
- C1.3 `7d202bd`: MCP-to-A2A skill catalog → catalog/schema tests;
  `Needs Verification`.
- C1.4 `4d625d4`: public Agent Card and endpoint projection → card tests;
  `Needs Verification`.
- C1.5 `10a44d3`: message/part mapping → mapping tests;
  `Needs Verification`.
- C1.6 `808e1eb`: run-status mapping → status tests;
  `Needs Verification`.
- C1.7 `5c4a6c3`: JSON-RPC server surface → A2A HTTP tests;
  `Needs Verification`.
- C1.8 `cc7b215`: server-core documentation → docs consistency tests;
  `Needs Verification`.

## Phase 2 — streaming and input-required recovery

- C2.1 `ca28adb`: stream-event normalization → A2A stream tests;
  `Needs Verification`.
- C2.2 `7502ac7`: progress projection → progress compatibility tests;
  `Needs Verification`.
- C2.3 `8b4304f`: streamed message results → SSE tests;
  `Needs Verification`.
- C2.4 `8472706`: run correlation persistence → correlation tests;
  `Needs Verification`.
- C2.5 `a828937`: owner-scoped task lookup → authorization tests;
  `Needs Verification`.
- C2.6 `c9e7df4`: input-required schemas → schema tests;
  `Needs Verification`.
- C2.7 `1558547`: resume input-required tasks → resume tests;
  `Needs Verification`.
- C2.8 `b93ff77`: streaming guidance → docs consistency tests;
  `Needs Verification`.
- Repair `99ac4bf`: Python 3.12 event-loop/fan-in repair and docs correction →
  cumulative Phase 2 gate was recorded in the plan; raw per-commit output is
  unavailable, so this row remains `Needs Verification`.

## Phase 3 — outbound MCP/A2A clients

- C3.1 `e911cc4`: interop registry/models → model and registry tests;
  `Needs Verification`.
- C3.2 `308b542`: endpoint, DNS/IP, credential, and HTTP boundary → transport,
  security, and registry tests; `Needs Verification`.
- C3.3 `e7037c3` (planned hash `7787fab`): official MCP adapter → MCP adapter
  and SDK contract tests; `Needs Verification`.
- C3.4 `14a3134` (planned hash `4c7497e`): capability DTO/cache → cache and
  capability tests; `Needs Verification`.
- C3.5 `70cd2f8` (planned hash `56d07d2`): A2A card discovery → discovery and
  SDK contract tests; `Needs Verification`.
- C3.6 `92cf394` (planned hash `58681c1`): A2A send/stream/mapping → client and
  mapping tests; `Needs Verification`.
- C3.7 `7f874fc` (planned hash `52c067d`): sanitized capability HTTP endpoint →
  interop HTTP tests; `Needs Verification`.
- C3.8 `3d53aff` (planned hash `db4b2f8`): trust/failure documentation → docs
  consistency and HTTP tests; `Needs Verification`.

## Phase 4 — Research/Design delegation

- C4.1 `c097e2e` (planned hash `b190820`): request controls → request-control
  tests; `Needs Verification`.
- C4.2 `8bd0d42` (planned hash `29a536e`): metadata-only planner → planner
  tests; `Needs Verification`.
- C4.3 `af93e9f` (planned hash `437b7a0`): Research/MCP delegation → research
  interop tests; `Needs Verification`.
- C4.4 `ff525c9` (planned hash `75f0e37`): Research/A2A delegation → research
  interop and resume tests; `Needs Verification`.
- C4.5 `466e543` (planned hash `e367940`): Design delegation → design interop
  tests; `Known incomplete` because the plan records a pre-fix R0801 failure
  and no post-fix full-gate rerun for this commit.
- C4.6 `5a20b8f` (planned hash `d570dad`): evidence/failure projection →
  design/research metadata and universal-failure tests; `Known incomplete`.
- C4.7 `15770c4` (planned hash `07f0127`): delegation semantics/docs matrix →
  mode-matrix and docs tests; `Known incomplete`.

## Phase 5 — explicit memory Store

- C5.1 `1d5df67` (planned hash `ea100cf`): storage-neutral models/policy →
  model and boundary tests; `Known incomplete`.
- C5.2 `c5b46b1` (planned hash `6bf1b06`): local SQLite store → store tests;
  `Known incomplete`.
- C5.3 `b62205f` (planned hash `b69cecf`): additive migrations/fail-closed
  validation → migration tests; `Known incomplete`.
- C5.4 `f3967ac` (planned hash `11b6a15`): authenticated memory CRUD → memory
  HTTP/config tests; `Known incomplete`.
- C5.5 `5cd9941` (planned hash `3738cef`): mutation audit → audit, migration,
  and store tests; `Known incomplete`.
- C5.6 `8f22f9d` (planned hash `854de04`): bounded graph accessor → accessor
  tests; `Known incomplete`.
- C5.7 `ddbc3ce` (planned hash `058e7f0`): Chat/Knowledge recall → prompt
  injection tests; `Needs Verification`.
- C5.8 `d5c5998` (planned hash `f6bfbb1`): privacy/retention enforcement →
  memory HTTP/store tests; `Needs Verification`.
- C5.9 `48152cc` (planned hash `080915e`): memory lifecycle docs → docs
  consistency tests; `Needs Verification`.

## Phase 6 — hardening and release handoff

- C6.1 `e4dfffa`: cross-surface flag/graph matrices → cross-surface and graph
  I/O tests; `Current-tree evidence` only.
- C6.2 `97fc4fd`: local fake-peer E2E → fake-peer interop suite;
  `Current-tree evidence` only.
- C6.3 `a41a12f`: interop state redaction → redaction tests;
  `Current-tree evidence` only.
- C6.4 `91332f0`: stream/store/cache limits → limit, registry, memory, and A2A
  executor tests; `Current-tree evidence` only.
- C6.5 `2d65954`: operator/migration runbooks → docs consistency tests;
  `Current-tree evidence` only.
- Gate repair `8c31573`: Pylint baseline documentation → current-tree full gate
  passed before `d025005`; it does not prove earlier commits.
- C6.6: no code commit. Wheel/version smoke passed. Live E2E was attempted with the
  integration/network flags and collected 33 items: `31 errors, 1 failed, 1 skipped`;
  local socket permission, backend DNS, and rerank network failures prevented a product
  verdict. The remote branch was subsequently fetched and rebased cleanly; final
  push evidence remains `Needs Verification`.

## Current audit-remediation commits

- `237e440`: CI Pylint parity and dependency-floor job semantics; `make scoped`
  passed with 467 focused/regression tests.
- `1c50b04`: tracked historical gate evidence summary; document checks and scoped
  gate passed. Historical rows remain `Needs Verification` where raw output is absent.
- `213b907`: relay audit body redaction, credential filtering, caps, route/store
  defense-in-depth, and regression coverage; `make scoped` passed with 501 tests.
- `d69ac89`: source-of-truth refresh and stale reducer test documentation;
  `make scoped` passed with 503 tests.
- `f347e79`: current commit IDs and the live-E2E environment failure classification;
  the code tree is unchanged by this evidence-only update.
- `0687e4f` and `7f15f5b`: record the live-E2E and push-verification boundaries;
  their histories were rebased onto remote commit `f2da19e` without conflicts.
- The first proxy-backed push probe reached the remote but was rejected as
  non-fast-forward because the remote had advanced; no merge or force-push was
  performed. The rebase was followed by `make push`, whose full gate passed with
  `2493 passed, 1 deselected`, 88.00% coverage, and all 110 module floors green;
  the remote now points at `8806f1d`.
- `e0e9e4a`, `21a0c69`, `37ded86`, and `8806f1d` record the rebased evidence,
  remote-config formatting repairs, prompt-render golden refresh, and final
  pushable test baseline. Their scoped gates passed; the final `make push`
  passed the full gate and updated the remote without merge or force-push.

These four commits were locally reworded before push to keep the public subject/body
convention and contain no local planning labels. Their message-only rewrites do not
change the code tree or prior gate results.

## Decision

The historical record is now explicit and auditable, but it is not a claim that
every earlier commit passed a reproducible full gate. Relay audit remediation and
source-of-truth refresh are complete. The rebase, SSH transport verification,
and post-rebase push are complete; the release remains blocked only by the
missing authorized-environment live E2E verdict.
