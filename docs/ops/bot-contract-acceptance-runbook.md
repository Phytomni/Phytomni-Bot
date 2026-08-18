# Bot Contract Acceptance Runbook

This runbook is the executable boundary for the five-handoff Bot contract
convergence work. It freezes one Bot commit, records deterministic local
evidence, and keeps deployment-owner acceptance separate from repository
tests. The convergence ledger is the status index; this document is the
current-SHA procedure.

## Status vocabulary

Use only these statuses in evidence packets and disposition records:

- `Unknown`: no reliable evidence has been collected.
- `Needs Verification`: an implementation or artifact exists, but the
  required current-SHA evidence is incomplete.
- `Bot Ready`: the Bot-local focused tests, full local gate, and supported
  Python matrix are green, with no unresolved P0.
- `External Pending`: Bot-local evidence is available, but a required
  Web/Go/staging/backend or owner check is absent.
- `Accepted`: the required Bot and external paired evidence is complete.
- `Blocked`: a required gate is red or an approved dependency prevents the
  next evidence step.
- `Rejected`: an owner or consumer explicitly rejected the contract.

### Bot Ready != Accepted

The shared wording lives in
[Bot Ready versus Accepted](bot-ready-versus-accepted.md). Bot Ready is
a repository readiness result. It does not authorize feature flags, Web
or Go cutover, production rollout, or migration cleanup. Any missing
paired consumer or staging evidence remains `External Pending`.

## Evidence boundary

The packet is immutable by Bot commit SHA and must contain only redacted,
synthetic contract evidence. Do not put API keys, bearer tokens, local home
paths, SQL, provider payloads, or live research results into tracked fixtures
or public evidence. The capture script writes outside the repository and
refuses an output directory inside the repository.

The following boundaries remain explicit:

- DataAgent exact-query replay and root-cause confirmation require backend
  authorization. Until then, DataAgent remains `External Pending` and no
  runtime behavior is changed to mask the incident.
- Analyst historical database matching and repair are a separate L2
  operation. New-run correlation can be Bot Ready while historical writes
  remain `External Pending`.
- Web, Go, staging, supported-version CI, and production acceptance are not
  closed by offline tests or a local full gate.
- A2UI and HTTP JSON files in this repository are shape goldens. They are not
  evidence that an external gateway forwarded a live request successfully.

## Current-SHA prerequisites

Run from the repository root on `release/0.1.4` (or record the actual branch
in the packet). Before freezing the SHA:

```bash
git status --short --branch
git rev-parse HEAD
```

The worktree must be clean except for paths explicitly documented with
`--allow-explained-path` when capturing evidence. Do not reuse a focused log,
full-gate log, or CI matrix from another SHA.

## Focused packet

The following command is the exact Bot-local acceptance packet:

```bash
uv run pytest \
  tests/unit/test_lifecycle_contract.py \
  tests/server/test_lifecycle_invariants_http.py \
  tests/server/test_agent_capabilities.py \
  tests/server/test_a2ui_actions_http.py \
  tests/server/test_a2ui_review_http.py \
  tests/server/test_a2ui_contract_fixtures.py \
  tests/server/test_a2ui_limits.py \
  tests/server/test_api_chat_streaming.py \
  tests/server/test_api_runs_status.py \
  tests/server/test_api_runs_list.py \
  tests/server/test_locale_http.py \
  tests/agents/test_locale_propagation.py \
  tests/unit/test_csv_upload_validation.py \
  tests/server/test_attachment_validation_http.py \
  tests/unit/test_artifact_roles.py \
  tests/unit/test_terminal_report.py \
  tests/server/test_scientific_execution_projection.py \
  tests/agents/test_expert_router.py \
  tests/server/test_query_route.py \
  tests/server/test_expert_contract_http.py \
  tests/unit/test_stage_trace.py \
  tests/server/test_data_agent_native_http.py \
  tests/agents/test_chat_a2ui_graph.py \
  tests/unit/test_contract_evidence_docs.py \
  tests/unit/scripts/test_capture_contract_evidence.py -q
```

Write `bot_sha=<40 lowercase hexadecimal characters>` before the test
output and exactly one `exit_code=<integer>` after it. The capture script
requires `exit_code=0` and verifies that the log SHA equals `HEAD`.

## Full local gate and Python matrix

After the focused packet passes, run the full local gate against the same
SHA:

```bash
UV_CACHE_DIR=/tmp/phytomni-uv-cache ./scripts/validate_local.sh
```

The supported-version matrix must have real `success` rows for Python 3.12,
3.13, and 3.14 on the same SHA. Missing, skipped, cancelled, or failed rows
are not green. If push or CI authorization is unavailable, record all three
rows as `External Pending`; do not synthesize matrix evidence or reuse an old
run. Recheck known version-specific failures on the final SHA rather than
carrying forward an earlier verdict.

Generate the packet only after the focused log, full-gate log, and matrix
JSON are available:

```bash
FINAL_SHA="$(git rev-parse HEAD)"
uv run python scripts/capture_contract_evidence.py \
  --focused-log "/tmp/focused-${FINAL_SHA}.log" \
  --full-gate-log "/tmp/full-gate-${FINAL_SHA}.log" \
  --python-matrix "/tmp/python-matrix-${FINAL_SHA}.json" \
  --output-root /tmp/phytomni-bot-contract-evidence
```

The expected packet is
`/tmp/phytomni-bot-contract-evidence/<bot_sha>/`. Inspect
`acceptance.json`, both logs, the two hash manifests, `python-matrix.json`,
and `worktree.txt` before recording a status.

## Contract assets

The current packet hashes these nine A2UI fixture files:

- `docs/contracts/a2ui/chat_confirm/downlink.json`
- `docs/contracts/a2ui/chat_form/downlink.json`
- `docs/contracts/a2ui/chat_choice/downlink.json`
- `docs/contracts/a2ui/chat_confirm/success_accept.json`
- `docs/contracts/a2ui/chat_form/success_submit.json`
- `docs/contracts/a2ui/chat_form/success_cancel.json`
- `docs/contracts/a2ui/chat_choice/success_submit.json`
- `docs/contracts/a2ui/chat_choice/success_cancel.json`
- `docs/contracts/a2ui/multi_turn/round2_downlink.json`

The HTTP body manifest covers these eighteen redacted goldens:

- `docs/contracts/http/a2ui_request_64k_exact.json`
- `docs/contracts/http/a2ui_request_64k_plus_one.json`
- `docs/contracts/http/a2ui_response_1m_exact.json`
- `docs/contracts/http/a2ui_response_1m_plus_one.json`
- `docs/contracts/http/chat_terminal_succeeded.json`
- `docs/contracts/http/analyst_terminal_succeeded.json`
- `docs/contracts/http/review_terminal_succeeded.json`
- `docs/contracts/http/review_round2_input_required.json`
- `docs/contracts/http/error_400_run_widget_payload_mismatch.json`
- `docs/contracts/http/error_404_owner_safe_not_found.json`
- `docs/contracts/http/error_409_already_handled.json`
- `docs/contracts/http/error_422_capability_validation.json`
- `docs/contracts/http/streamed_run_accumulated_answer.json`
- `docs/contracts/http/deep_genome_bounded_reports.json`
- `docs/contracts/http/dataagent_incident_replay_guarded.json`
- `docs/contracts/http/expert_local_edge_contract.json`
- `docs/contracts/http/remote_partial_acceptance.json`
- `docs/contracts/http/remote_registry_degraded.json`

The tracked-doc test locks the exact fixture set and rejects credential,
local-path, SQL, provider-payload, and private-result markers.

## Staging smoke packet

These twelve smokes require deployment-owner evidence. They are listed here
so a later Web/Go/staging packet can report the same names without changing
the acceptance vocabulary:

1. **Exact DataAgent cDNA question**: submit the frozen business question and
   capture the stage trace and terminal result.
1. **Review pause, classic resume, and A2UI action**: exercise both resume
   paths and verify first-uplink-wins behavior.
1. **Expert autonomous and forced**: compare autonomous selection with an
   explicitly forced, granted route.
1. **Expert attachment whitelist**: verify an allowed attachment reaches the
   selected route and an unsupported one is rejected.
1. **Forced ungranted tool rejected before upload/dispatch**: verify the
   admission failure has no upload or downstream dispatch side effect.
1. **Strict router outside allowlist returns 502 and zero dispatch**: verify
   the contract error and the absence of an upstream call.
1. **Strict decline degrades to chat only when allowed**: verify a model
   decline (no tool call) dispatches ChatAgent with the injected `user_query`
   when `allowed_tools` includes `ChatAgent`, and returns 502 with zero
   dispatch when it does not.
1. **Instant makes no /v1/query/route call**: verify direct Instant execution
   does not invoke the compatibility router.
1. **Literal @Agent in Instant has no routing side effect**: verify ordinary
   message text does not become a routing command.
1. **Remote partial acceptance**: verify accepted child tasks and rejected
   child tasks remain distinguishable in the response.
1. **Registry-degraded accepted-task response**: verify a successful remote
   task with failed local bookkeeping carries `degraded_tracking`.
1. **Final reports for Analyst, Research, Design, Network, and DeepGenome**:
   verify each report state, artifact role, and bounded projection.
1. **Request-to-run-to-task-to-stage correlation**: verify the identifiers
   can be joined without substituting a child task ID for the umbrella run.

Until these smokes return redacted artifacts for the same contract hashes,
the corresponding migration rows remain `External Pending`.

## Gate interpretation

Use the following exact decision rules:

```text
focused green + full green + matrix green + no P0 -> Bot Ready
Bot Ready without Web/Go/staging -> External Pending, not Accepted
Data root-cause unresolved -> Bot Ready blocked
Python 3.13 or 3.14 red -> Bot Ready blocked
historical Analyst write pending -> does not block Bot code, remains separate L2
```

A local failure is `Blocked` until repaired and rechecked from a new SHA. A
missing external artifact is `External Pending`, not `Accepted`. Keep all
feature flags dark while a cutover row is external or blocked.

## Rollback and escalation

Rollback is commit- and flag-scoped. Revert only the narrow contract or
projection commit named by the ledger row, restore the associated flag to its
previous value, and rerun the focused packet. Do not delete evidence or
rewrite a prior SHA packet. DataAgent backend replay, Analyst historical
repair, Web/Go migration, and production activation require their respective
owners to authorize the next step.
