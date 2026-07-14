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

- Before the CI-alignment commit, `f7ad495` passed the authoritative local gate:
  `2488 passed, 1 deselected`, 87.96% coverage, and 110 agent module floors.
- The CI-equivalent Pylint invocation now passes with
  `--disable=R0801,R0903`; the independent baseline check reports
  `R0801=124, R0903=28` at baseline.
- Commit `d1aabe6` passed `make scoped`: secret scan, Python tooling, workflow
  checks, Markdown checks, and 467 focused/regression tests.
- A full gate after `d1aabe6` remains a final release-evidence task; it is not
  backdated to any historical commit.

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

- C3.1 `7299320`: interop registry/models → model and registry tests;
  `Needs Verification`.
- C3.2 `b684fd4`: endpoint, DNS/IP, credential, and HTTP boundary → transport,
  security, and registry tests; `Needs Verification`.
- C3.3 `77ef517` (planned hash `7787fab`): official MCP adapter → MCP adapter
  and SDK contract tests; `Needs Verification`.
- C3.4 `ab5c6b1` (planned hash `4c7497e`): capability DTO/cache → cache and
  capability tests; `Needs Verification`.
- C3.5 `7c23ea4` (planned hash `56d07d2`): A2A card discovery → discovery and
  SDK contract tests; `Needs Verification`.
- C3.6 `ad66b4d` (planned hash `58681c1`): A2A send/stream/mapping → client and
  mapping tests; `Needs Verification`.
- C3.7 `ca413ae` (planned hash `52c067d`): sanitized capability HTTP endpoint →
  interop HTTP tests; `Needs Verification`.
- C3.8 `eb75e9e` (planned hash `db4b2f8`): trust/failure documentation → docs
  consistency and HTTP tests; `Needs Verification`.

## Phase 4 — Research/Design delegation

- C4.1 `90fa987` (planned hash `b190820`): request controls → request-control
  tests; `Needs Verification`.
- C4.2 `6f99cda` (planned hash `29a536e`): metadata-only planner → planner
  tests; `Needs Verification`.
- C4.3 `0edb607` (planned hash `437b7a0`): Research/MCP delegation → research
  interop tests; `Needs Verification`.
- C4.4 `3f91a4f` (planned hash `75f0e37`): Research/A2A delegation → research
  interop and resume tests; `Needs Verification`.
- C4.5 `050c70e` (planned hash `e367940`): Design delegation → design interop
  tests; `Known incomplete` because the plan records a pre-fix R0801 failure
  and no post-fix full-gate rerun for this commit.
- C4.6 `6391db6` (planned hash `d570dad`): evidence/failure projection →
  design/research metadata and universal-failure tests; `Known incomplete`.
- C4.7 `411e183` (planned hash `07f0127`): delegation semantics/docs matrix →
  mode-matrix and docs tests; `Known incomplete`.

## Phase 5 — explicit memory Store

- C5.1 `ec6d2de` (planned hash `ea100cf`): storage-neutral models/policy →
  model and boundary tests; `Known incomplete`.
- C5.2 `2729003` (planned hash `6bf1b06`): local SQLite store → store tests;
  `Known incomplete`.
- C5.3 `5f66663` (planned hash `b69cecf`): additive migrations/fail-closed
  validation → migration tests; `Known incomplete`.
- C5.4 `ea8d4a9` (planned hash `11b6a15`): authenticated memory CRUD → memory
  HTTP/config tests; `Known incomplete`.
- C5.5 `a8515a5` (planned hash `3738cef`): mutation audit → audit, migration,
  and store tests; `Known incomplete`.
- C5.6 `d3b18f0` (planned hash `854de04`): bounded graph accessor → accessor
  tests; `Known incomplete`.
- C5.7 `4bcdf13` (planned hash `058e7f0`): Chat/Knowledge recall → prompt
  injection tests; `Needs Verification`.
- C5.8 `3391708` (planned hash `f6bfbb1`): privacy/retention enforcement →
  memory HTTP/store tests; `Needs Verification`.
- C5.9 `9e4d3bc` (planned hash `080915e`): memory lifecycle docs → docs
  consistency tests; `Needs Verification`.

## Phase 6 — hardening and release handoff

- C6.1 `e787b74`: cross-surface flag/graph matrices → cross-surface and graph
  I/O tests; `Current-tree evidence` only.
- C6.2 `90ac4f1`: local fake-peer E2E → fake-peer interop suite;
  `Current-tree evidence` only.
- C6.3 `652cef7`: interop state redaction → redaction tests;
  `Current-tree evidence` only.
- C6.4 `2ab14a4`: stream/store/cache limits → limit, registry, memory, and A2A
  executor tests; `Current-tree evidence` only.
- C6.5 `5a4740e`: operator/migration runbooks → docs consistency tests;
  `Current-tree evidence` only.
- Gate repair `f7ad495`: Pylint baseline documentation → current-tree full gate
  passed before `d025005`; it does not prove earlier commits.
- C6.6: no code commit. Wheel/version smoke passed. Live E2E was attempted with the
  integration/network flags and collected 33 items: `31 errors, 1 failed, 1 skipped`;
  local socket permission, backend DNS, and rerank network failures prevented a product
  verdict. Push remains unverified; `Needs Verification`.

## Current audit-remediation commits

- `d1aabe6`: CI Pylint parity and dependency-floor job semantics; `make scoped`
  passed with 467 focused/regression tests.
- `bea0293`: tracked historical gate evidence summary; document checks and scoped
  gate passed. Historical rows remain `Needs Verification` where raw output is absent.
- `574cc9d`: relay audit body redaction, credential filtering, caps, route/store
  defense-in-depth, and regression coverage; `make scoped` passed with 501 tests.
- `aa189a4`: source-of-truth refresh and stale reducer test documentation;
  `make scoped` passed with 503 tests.
- `d0d2dc8`: current commit IDs and the live-E2E environment failure classification;
  the code tree is unchanged by this evidence-only update.

These four commits were locally reworded before push to keep the public subject/body
convention and contain no local planning labels. Their message-only rewrites do not
change the code tree or prior gate results.

## Decision

The historical record is now explicit and auditable, but it is not a claim that
every earlier commit passed a reproducible full gate. Relay audit remediation and
source-of-truth refresh are complete. The release remains blocked until the final
full gate, authorized live E2E, and successful `make push` are complete.
