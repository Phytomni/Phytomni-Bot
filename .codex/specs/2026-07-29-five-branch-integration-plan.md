# Five Branch Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the complete local
`codex/five-sync-agent-multiturn` history into the moving
`release/0.1.4` branch, preserve unrelated work, verify the landed merge, and
delete only the integrated source branch and its worktree.

**Architecture:** Build and test the merge on the isolated
`codex/five-integration` worktree. Converge on a moving target with bounded
full-gate rounds, land only through `git merge --ff-only`, restore the target
worktree from a dedicated safety stash, and clean up only after ancestry and
worktree-state verification.

**Tech Stack:** Git, zsh, CodeGraph, Python 3.12-3.14, uv, pytest, and
`scripts/validate_local.sh`.

## Current Execution Record (2026-07-31)

The live refs supersede the initial dry-merge assumptions above. The target
was rechecked at `0fe6a9cd9460f4bbbc9eb1f642787351190e1428`, and the source
tip is `4ecc06f47bb2eeaf7ef2018a8d8310bd5dba87a1`. The source is already a
full ancestor of `release/0.1.4` (`git rev-list --left-right --count` is
`264 0`), so no second merge commit, integration branch, or cherry-pick is
required. The release branch retains the complete source history and carries
the subsequent gate-repair commits on top.

The current convergence work has added compatibility-preserving fixes for
the merged Data/Knowledge contracts, background settlement, Markdown gate
parsing, OBS warning handling, and conversation-context coverage. The final
full gate is still required on the post-record HEAD before cleanup. Until
that proof is captured, retain the exact source worktree, source branch, and
the five audited pre-existing stashes. The detached registration for
`/tmp/phytomni-bot-mcp-gate` is stale and may be pruned only after the final
gate.

## Global Constraints

- Target branch: `release/0.1.4`.
- Source branch: `codex/five-sync-agent-multiturn`.
- Integration branch: `codex/five-integration`.
- Target worktree:
  `/home/xieshang/Workdir/1.phytomni/Phytomni-Bot`.
- Source worktree:
  `/home/xieshang/Workdir/1.phytomni/Phytomni-Web/.worktrees/Phytomni-Bot-five-sync`.
- Integration worktree:
  `/tmp/phytomni-bot-five-integration.pZsgRg`.
- Preserve both branch histories with a merge commit. Do not squash, rebase
  the source branch, or cherry-pick its commits.
- Do not push, fetch, delete remote refs, or modify tracked sibling-repository
  content.
- Preserve every pre-existing stash and all unrelated staged, unstaged,
  untracked, and ignored files.
- Never use `git reset --hard`, a force update, `--no-verify`, blanket
  whole-file `ours`/`theirs`, or broad `git worktree prune`.
- Run the source full gate before merging and the merged-result full gate
  before landing.
- Allow at most three merged-result convergence gates. Persistent target
  movement after the third requires a short writer pause.
- Delete the source branch and source worktree only after the landed merge
  and exact source tip are ancestors of the current target.
- Gate claims apply to the tested merge SHA. Later concurrent descendant
  commits are reported separately.
- Command blocks that share shell variables must run in the same zsh session.
  If execution tooling starts a fresh shell, rerun the canonical path/branch
  assignments and re-resolve dynamic refs; never guess or reuse an unverified
  SHA from terminal scrollback.

______________________________________________________________________

## File Structure and Merge Responsibilities

The source branch contributes its complete non-conflicting tree unchanged.
Manual edits are limited to files Git reports as conflicted after the actual
merge. The design-time dry merge identified these responsibilities:

- `src/mcp_server_phytomni/agents/data/agent.py`
  - retain target-side `DataStage` tracing and safe result metadata;
  - retain source-side stable `dialog_id` propagation from graph state.
- `src/mcp_server_phytomni/api/app.py`
  - retain target-side preflight, background-submission, safe-error, async
    sync-settlement, and Data result-format tracing;
  - retain source-side private conversation messages, agent thread ID,
    private agent state, and Review context-adapter bypass.
- `src/mcp_server_phytomni/mcp/app.py`
  - keep `validate_tool_arguments()` as the single validation seam;
  - extend `invoke_tool_raw()` and `invoke_tool_enveloped()` with private,
    keyword-only context and guaranteed contextvar reset.
- `tests/server/test_mcp_app_invoke.py`
  - retain target validation-seam tests and source private-history isolation
    tests.
- `tests/server/test_query_route.py`
  - retain target background-run helpers/imports and source conversation
    context fixtures/imports.
- `.codex/specs/2026-07-29-five-branch-integration-design.md`
  - approved integration contract; do not change during execution unless a
    discovered repository fact contradicts it.
- `.codex/specs/2026-07-29-five-branch-integration-plan.md`
  - this execution checklist and evidence boundary.

The exact conflict list is dynamic. Recalculate it against the target tip
used for the candidate and add any newly conflicted path to the same semantic
review, focused-test, and staged-diff checks.

### Interfaces that must survive the merge

```python
invoke_tool_raw(
    name: Any,
    arguments: dict[str, Any],
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
    agent_thread_id: str | None = None,
    private_agent_state: Mapping[str, Any] | None = None,
) -> Any


invoke_tool_enveloped(
    name: Any,
    arguments: dict[str, Any],
    *,
    conversation_messages: Sequence[Mapping[str, str]] = (),
    agent_thread_id: str | None = None,
    private_agent_state: Mapping[str, Any] | None = None,
) -> ToolResultEnvelope


_invoke_agent_run(
    *,
    agent: str,
    arguments: dict[str, Any],
    conversation_messages: tuple[dict[str, str], ...] = (),
    agent_thread_id: str | None = None,
    private_agent_state: Mapping[str, Any] | None = None,
    dialogue_id: str | None = None,
    request_json: str | None = None,
    debug: bool = False,
) -> tuple[dict[str, Any], int]


DataAgent.arun(
    self,
    user_query: str,
    is_rewrite: bool = True,
    dialog_id: str | None = None,
    thread_id: str | None = None,
    locale: SupportedLocale | None = None,
)
```

Private conversation inputs must remain absent from public Pydantic MCP
schemas. `validate_tool_arguments()` must remain independently callable
without invoking a handler.

______________________________________________________________________

### Task 1: Capture the live baseline and verify the source branch

**Files:**

- Inspect only: Git refs, worktree registrations, and source worktree.
- Test: `scripts/validate_local.sh`.

**Interfaces:**

- Consumes: the three local branch refs and registered worktrees.

- Produces: immutable `SOURCE_SHA`, initial `TARGET_SHA`, clean-source proof,
  merge-base evidence, and a green source-gate result.

- [ ] **Step 1: Capture exact refs and paths**

Run from the target worktree in one zsh session:

```bash
SOURCE_BRANCH=codex/five-sync-agent-multiturn
TARGET_BRANCH=release/0.1.4
INTEGRATION_BRANCH=codex/five-integration
TARGET_WORKTREE=/home/xieshang/Workdir/1.phytomni/Phytomni-Bot
SOURCE_WORKTREE=/home/xieshang/Workdir/1.phytomni/Phytomni-Web/.worktrees/Phytomni-Bot-five-sync
INTEGRATION_WORKTREE=/tmp/phytomni-bot-five-integration.pZsgRg

SOURCE_SHA=$(git rev-parse "$SOURCE_BRANCH")
TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")
MERGE_BASE_SHA=$(git merge-base "$TARGET_SHA" "$SOURCE_SHA")

git show -s --format='%H %s' "$TARGET_SHA" "$SOURCE_SHA"
git rev-list --left-right --count "$TARGET_SHA"..."$SOURCE_SHA"
git worktree list --porcelain
```

Expected:

- all three branches resolve;

- the target and source remain divergent unless another integration already
  landed; and

- both named worktrees are registered at their declared paths.

- [ ] **Step 2: Verify source and integration worktrees are clean**

Run:

```bash
git -C "$SOURCE_WORKTREE" status --short --branch
git -C "$INTEGRATION_WORKTREE" status --short --branch
test -z "$(git -C "$SOURCE_WORKTREE" status --porcelain)"
test -z "$(git -C "$INTEGRATION_WORKTREE" status --porcelain)"
```

Expected: both porcelain outputs are empty. If either contains a path, stop;
do not stash or absorb that work.

- [ ] **Step 3: Check whether the source is already integrated**

Run:

```bash
git merge-base --is-ancestor "$SOURCE_SHA" "$TARGET_SHA"
rc=$?
case "$rc" in
  0) SOURCE_ALREADY_INTEGRATED=1 ;;
  1) SOURCE_ALREADY_INTEGRATED=0 ;;
  *) exit "$rc" ;;
esac
```

Expected:

- `rc=1`: set `SOURCE_ALREADY_INTEGRATED=0` and continue with the merge; or
- `rc=0`: set `SOURCE_ALREADY_INTEGRATED=1`; Task 2 still re-anchors the
  approved documentation commits, but skips the source merge/conflict steps.
  Tasks 3 and 4 then gate and land that documentation-only descendant before
  Task 5 cleanup.

Any other exit code is a Git error and stops the workflow.

- [ ] **Step 4: Run the full gate on the exact source tip**

Run:

```bash
cd "$SOURCE_WORKTREE"
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
UV_CACHE_DIR=/tmp/phytomni-five-source-cache \
  ./scripts/validate_local.sh
```

Expected: exit `0`. Record the source SHA and gate summary. A failure blocks
the merge and source deletion.

- [ ] **Step 5: Reconfirm the source did not move during its gate**

Run:

```bash
test "$(git rev-parse "$SOURCE_BRANCH")" = "$SOURCE_SHA"
test "$(git -C "$SOURCE_WORKTREE" rev-parse HEAD)" = "$SOURCE_SHA"
test -z "$(git -C "$SOURCE_WORKTREE" status --porcelain)"
```

Expected: all three checks pass. A moved or dirty source invalidates the gate
and requires restarting Task 1 with a new `SOURCE_SHA`.

______________________________________________________________________

### Task 2: Anchor the integration branch and create the semantic merge

**Files:**

- Modify if conflicted:
  `src/mcp_server_phytomni/agents/data/agent.py`.
- Modify if conflicted: `src/mcp_server_phytomni/api/app.py`.
- Modify if conflicted: `src/mcp_server_phytomni/mcp/app.py`.
- Modify if conflicted: `tests/server/test_mcp_app_invoke.py`.
- Modify if conflicted: `tests/server/test_query_route.py`.
- Test: `tests/agents/test_data_agent.py`.
- Test: `tests/server/test_mcp_app_invoke.py`.
- Test: `tests/server/test_query_route.py`.
- Test: `tests/server/test_api_agent_runs.py`.
- Test: `tests/server/test_conversation_context_routes.py`.
- Test: `tests/unit/runtime/conversation_context/`.

**Interfaces:**

- Consumes: Task 1's immutable source tip and the current target tip.

- Produces: one merge commit containing both complete histories and all
  semantically combined conflict resolutions, or a documentation-only
  descendant when the source was already integrated.

- [ ] **Step 1: Re-anchor documentation commits on the latest target tip**

Run in the integration worktree:

```bash
cd "$INTEGRATION_WORKTREE"
test -z "$(git status --porcelain)"

TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")
test "$(git rev-parse "$SOURCE_BRANCH")" = "$SOURCE_SHA"
git merge-base --is-ancestor "$SOURCE_SHA" "$TARGET_SHA"
rc=$?
case "$rc" in
  0) SOURCE_ALREADY_INTEGRATED=1 ;;
  1) SOURCE_ALREADY_INTEGRATED=0 ;;
  *) exit "$rc" ;;
esac

DESIGN_COMMIT=$(
  git log -n 1 --format='%H' \
    --grep '^📋 Docs: Define five branch integration$' \
    "$INTEGRATION_BRANCH"
)
DOC_BASE_SHA=$(git rev-parse "$DESIGN_COMMIT^")

if ! git merge-base --is-ancestor "$TARGET_SHA" "$INTEGRATION_BRANCH"; then
  git merge-base --is-ancestor "$DOC_BASE_SHA" "$TARGET_SHA"
  git rebase --onto "$TARGET_SHA" "$DOC_BASE_SHA" "$INTEGRATION_BRANCH"
fi

test "$(git merge-base "$INTEGRATION_BRANCH" "$TARGET_BRANCH")" = "$TARGET_SHA"
git log --oneline "$TARGET_SHA"..HEAD
```

Expected: only the approved design and plan commits appear above the captured
target. This rebase is allowed only for integration-local documentation
commits; never rebase the source or target branch.

When `SOURCE_ALREADY_INTEGRATED=1`, set:

```bash
MERGE_SHA=$(git rev-parse HEAD)
git merge-base --is-ancestor "$SOURCE_SHA" "$MERGE_SHA"
```

Then skip Task 2 Steps 2-10 and continue to Task 3.

- [ ] **Step 2: Recalculate the conflict set**

Run:

```bash
TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")
MERGE_BASE_SHA=$(git merge-base "$TARGET_SHA" "$SOURCE_SHA")

git merge-tree "$MERGE_BASE_SHA" "$TARGET_SHA" "$SOURCE_SHA" |
  awk '/^  their / { path=$NF } /<<<<<<< / { print path }' |
  sort -u
```

Expected at the approved baseline: the five conflict files listed in the
file map. Treat this command's current output as authoritative if the target
has advanced.

- [ ] **Step 3: Start a non-squash source merge without committing**

Run:

```bash
git merge --no-ff --no-commit "$SOURCE_SHA"
rc=$?
git diff --name-only --diff-filter=U
```

Expected: `rc=1` with unresolved paths matching the recalculated conflict set.
If `rc=0`, Git merged automatically; still review every path changed on both
sides before testing. Any fatal Git error stops the task without committing.

- [ ] **Step 4: Resolve `DataAgent` without losing either contract**

Before editing, query CodeGraph against the target checkout:

```bash
codegraph explore \
  "DataAgent search_node arun Nl2SqlRequest trace_data_stage"
```

Then edit the conflicted integration-worktree file with `apply_patch` so:

```python
async def search_node(self, state: DataAgentState):
    async with trace_data_stage(
        DataStage.NL2SQL_REQUEST,
        dependency="nl2sql",
    ):
        request = Nl2SqlRequest.from_kwargs(
            query,
            {
                "database_url": self.data_config.DATABASE_URL,
                "workspace_id": self.data_config.WORKSPACE_ID,
                "subject_id": self.data_config.SUBJECT_ID,
                "dialog_id": (
                    state.get("dialog_id")
                    or self.data_config.DIALOG_ID
                    or _default_dialog_id()
                ),
                "need_insight": self.data_config.NEED_INSIGHT,
                "simplify_response": self.data_config.SIMPLIFY_RESPONSE,
                "timeout": self.data_config.TIMEOUT,
                "retriable_codes": self.data_config.RETRIABLE_CODES,
                "max_retries": self.data_config.MAX_RETRIES,
            },
        )
    async with trace_data_stage(
        DataStage.DATABASE_QUERY,
        dependency="database",
    ):
        result = await execute_nl2sql_request(request)
        if result is None:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="No response received from SQL database",
                )
            )
    logger.debug(
        "DataAgent result received",
        extra={
            "request_id": current_request_id() or "unknown",
            "agent": "data",
            "stage": DataStage.DATABASE_QUERY.value,
            "result_kind": _result_kind(result),
            "row_count": _result_row_count(result),
        },
    )
    return {"final_response": result}
```

Retain the source signature's `dialog_id` argument in `DataAgent.arun()` and
seed `"dialog_id": dialog_id` into the initial state.

- [ ] **Step 5: Resolve raw/enveloped MCP invocation**

Before editing, query:

```bash
codegraph explore \
  "validate_tool_arguments invoke_tool_raw invoke_tool_enveloped dispatch_tool"
```

Apply these exact rules:

1. Keep target `validate_tool_arguments()` unchanged as the single model and
   handler-existence validation seam.

1. Use the private keyword-only signatures from the Interfaces section.

1. In `invoke_tool_raw()`, call:

   ```python
   tool_name = _tool_name(name)
   args = validate_tool_arguments(tool_name, arguments)
   ```

1. Bind all three private contextvars before handler invocation.

1. Invoke `TOOL_HANDLERS[tool_name](args)` once.

1. Reset private state, thread ID, and messages in a `finally` block.

1. Forward all three private values from `invoke_tool_enveloped()` to
   `invoke_tool_raw()`.

1. Keep citation enrichment and envelope construction unchanged.

Do not add private fields to any public MCP request model.

- [ ] **Step 6: Resolve HTTP `_invoke_agent_run()`**

Before editing, query:

```bash
codegraph explore \
  "_preflight_agent_run _prepare_agent_run _invoke_agent_run \
_background_agent_run_response _sync_agent_run_response"
```

Apply these exact rules:

1. Use the full private-context signature from the Interfaces section.

1. Keep target `_preflight_agent_run()` before all dispatch.

1. Keep target background-agent early return and
   `BackgroundSubmissionLaunchError` projection.

1. Keep target `_prepare_agent_run()` after the background branch.

1. Calculate:

   ```python
   context_review = (
       isinstance(private_agent_state, Mapping)
       and private_agent_state.get("review_adapter") is not None
   )
   ```

1. Enter `_run_review_with_interrupt()` only for Review without the private
   context adapter.

1. Inside the target's `try` block, call `invoke_tool_enveloped()` with all
   three private context arguments.

1. Keep target Data result-format tracing, remote-agent response projection,
   awaited `_sync_agent_run_response()`, `SafeApiError` passthrough, and
   Data-stage safe-error mapping.

- [ ] **Step 7: Combine both test suites at conflict boundaries**

In `tests/server/test_mcp_app_invoke.py`, retain:

- `test_validate_tool_arguments_does_not_invoke_handler`;
- validation-error coverage;
- `test_v1_history_reaches_chat_handler_through_raw_dispatch`;
- `test_v1_history_reaches_expert_handler_through_raw_dispatch`; and
- `test_v1_history_reaches_knowledge_wrapper_without_leakage`.

In `tests/server/test_query_route.py`, retain:

- both `asyncio` and `json` imports;
- `RunRecord` and `RunRegistry`;
- `ConversationContextStore` and
  `context_agent_thread_id`;
- the complete `_wait_for_run_children()` helper; and
- `_REVIEW_REPORT`, `_review_checkpoint_state()`,
  `_review_context_envelope()`, and `_patch_review_runtime()`.

Preserve import ordering and the repository's 79-column limit.

- [ ] **Step 8: Prove every conflict is resolved and stage only the merge**

Run:

```bash
git diff --name-only --diff-filter=U |
  while IFS= read -r conflict_path; do
    rg -n '^(<<<<<<<|=======|>>>>>>>)' "$conflict_path"
    rc=$?
    test "$rc" -eq 1
  done
git diff --check
git diff --name-only --diff-filter=U -z |
  xargs -0 -r git add --
git diff --name-only --diff-filter=U
git diff --cached --check
git status --short
```

Expected:

- ripgrep exits `1` with no marker output;
- `git diff --check` exits `0`;
- no unmerged paths remain after `git add`; and
- the status contains only the source merge plus intentional conflict
  resolutions.

The null-delimited `git add` stages every current conflict path, including a
new conflict introduced by a target advance. Git already stages
non-conflicting source paths during the merge. Do not add unrelated files
outside the merge result.

- [ ] **Step 9: Run focused conflict-boundary tests**

Run:

```bash
UV_CACHE_DIR=/tmp/phytomni-five-merge-cache \
  uv run pytest \
    tests/agents/test_data_agent.py \
    tests/server/test_mcp_app_invoke.py \
    tests/server/test_query_route.py \
    tests/server/test_api_agent_runs.py \
    tests/server/test_conversation_context_routes.py \
    tests/unit/runtime/conversation_context/ \
    -v
```

Expected: all selected tests pass. Any failure must be resolved in the merge
before committing.

- [ ] **Step 10: Commit the semantic merge**

Run:

```bash
git commit \
  -m '🔀 Merge: Integrate conversation context branch' \
  -m '- context spot: integrate bounded V1 conversation state and multi-turn \
agent behavior.' \
  -m '- runtime spot: retain background submission, safe error, tracing, and \
envelope contracts.' \
  -m '- test spot: combine validation, context isolation, routing, and \
DataAgent regressions.' \
  -m '- provenance spot: preserve the complete source and release histories \
without squashing.'
```

Expected when `SOURCE_ALREADY_INTEGRATED=0`: the secret-scan hook passes and
Git creates a two-parent merge commit.

Verify:

```bash
MERGE_SHA=$(git rev-parse HEAD)
git rev-list --parents -n 1 "$MERGE_SHA"
git merge-base --is-ancestor "$SOURCE_SHA" "$MERGE_SHA"
git merge-base --is-ancestor "$TARGET_SHA" "$MERGE_SHA"
test -z "$(git status --porcelain)"
```

______________________________________________________________________

### Task 3: Run the merged gate and converge on the moving target

**Files:**

- Modify only if a later target merge introduces a semantic conflict.
- Test: `scripts/validate_local.sh`.

**Interfaces:**

- Consumes: Task 2's merge SHA and the live target ref.

- Produces: `VERIFIED_CANDIDATE_SHA`, `TESTED_TARGET_SHA`, and a full-gate
  result no more than three convergence rounds old.

- [ ] **Step 1: Initialize the bounded convergence loop**

Run in the integration worktree:

```bash
convergence_round=1
# Keep the Task 2 value: it names the target tip already in the candidate.
CANDIDATE_TARGET_SHA="$TARGET_SHA"

git merge-base --is-ancestor "$CANDIDATE_TARGET_SHA" HEAD
git merge-base --is-ancestor "$SOURCE_SHA" HEAD
```

Expected: the initial semantic merge contains Task 2's target snapshot and
the immutable source tip.

- [ ] **Step 2: Incorporate the target tip current before this round's gate**

Run:

```bash
CURRENT_TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")

if test "$CURRENT_TARGET_SHA" != "$CANDIDATE_TARGET_SHA"; then
  git merge-base --is-ancestor \
    "$CANDIDATE_TARGET_SHA" "$CURRENT_TARGET_SHA"
  git merge --no-ff --no-commit "$CURRENT_TARGET_SHA"
fi
```

If the ancestry check fails, abort any in-progress merge and stop: the target
was rewritten non-fast-forward.

When a convergence merge started, resolve every conflict semantically after
querying the affected symbols with CodeGraph. Run the focused tests for every
affected seam, then run:

```bash
git diff --name-only --diff-filter=U |
  while IFS= read -r conflict_path; do
    rg -n '^(<<<<<<<|=======|>>>>>>>)' "$conflict_path"
    rc=$?
    test "$rc" -eq 1
  done
git diff --name-only --diff-filter=U -z |
  xargs -0 -r git add --
git diff --name-only --diff-filter=U
git diff --check
git commit \
  -m '🔀 Merge: Converge release branch updates' \
  -m '- target spot: incorporate the latest normal release/0.1.4 advance.' \
  -m '- integration spot: retain the verified conversation-context merge \
ancestry.' \
  -m '- verification spot: rerun affected focused tests before the next full gate.'
CANDIDATE_TARGET_SHA="$CURRENT_TARGET_SHA"
```

Expected: a clean integration worktree whose HEAD contains the latest target
and source tips.

- [ ] **Step 3: Run this round's full merged-result gate**

Run:

```bash
TESTED_TARGET_SHA="$CANDIDATE_TARGET_SHA"
git merge-base --is-ancestor "$TESTED_TARGET_SHA" HEAD
git merge-base --is-ancestor "$SOURCE_SHA" HEAD
git diff --check "$TESTED_TARGET_SHA"...HEAD
UV_CACHE_DIR=/tmp/phytomni-five-merge-cache \
  ./scripts/validate_local.sh
```

Expected: all checks and the full gate pass. Record `convergence_round`, the
candidate SHA, the tested target SHA, and the gate summary.

- [ ] **Step 4: Classify movement after the gate and repeat if needed**

Run:

```bash
AFTER_GATE_TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")

if test "$AFTER_GATE_TARGET_SHA" = "$TESTED_TARGET_SHA"; then
  VERIFIED_CANDIDATE_SHA=$(git rev-parse HEAD)
elif git merge-base --is-ancestor \
  "$TESTED_TARGET_SHA" "$AFTER_GATE_TARGET_SHA"; then
  if test "$convergence_round" -ge 3; then
    rc=4
  else
    convergence_round=$((convergence_round + 1))
    CANDIDATE_TARGET_SHA="$TESTED_TARGET_SHA"
    rc=2
  fi
else
  rc=3
fi
```

Interpretation:

- equality: continue to Step 5;
- `rc=2`: return to Step 2, which incorporates
  `AFTER_GATE_TARGET_SHA`, then run the next full gate;
- `rc=3`: stop because the target was rewritten non-fast-forward; or
- `rc=4`: stop before landing and request a short writer pause.

Stop conditions:

- target stable after a green round: continue;

- target advances after convergence round 3: stop before landing and request
  a short writer pause;

- non-fast-forward target rewrite: stop and retain all branches/worktrees;

- any gate failure: stop and retain the candidate for diagnosis.

- [ ] **Step 5: Capture final candidate evidence**

Run:

```bash
test "$(git rev-parse HEAD)" = "$VERIFIED_CANDIDATE_SHA"
git merge-base --is-ancestor "$SOURCE_SHA" "$VERIFIED_CANDIDATE_SHA"
git merge-base --is-ancestor \
  "$TESTED_TARGET_SHA" "$VERIFIED_CANDIDATE_SHA"
test -z "$(git status --porcelain)"
git show -s --format='%H%n%P%n%s%n%b' "$VERIFIED_CANDIDATE_SHA"
```

Expected: both ancestry checks pass, the integration worktree is clean, and
the recorded SHA is exactly the green candidate.

______________________________________________________________________

### Task 4: Land the verified candidate and restore target WIP

**Files:**

- Preserve: every path reported by the target worktree's pre-landing
  `git status --short`.
- Modify: local `release/0.1.4` ref through fast-forward only.
- Preserve: every pre-existing stash.

**Interfaces:**

- Consumes: Task 3's verified candidate and tested target SHA.

- Produces: landed merge SHA, restored WIP state, and post-landing ancestry
  classification.

- [ ] **Step 1: Enter the short landing critical section**

Run from the target worktree in one zsh session:

```bash
cd "$TARGET_WORKTREE"

LAND_BASE_SHA=$(git rev-parse HEAD)
CURRENT_TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")
test "$LAND_BASE_SHA" = "$CURRENT_TARGET_SHA"
test "$CURRENT_TARGET_SHA" = "$TESTED_TARGET_SHA"
git merge-base --is-ancestor \
  "$CURRENT_TARGET_SHA" "$VERIFIED_CANDIDATE_SHA"

PRE_WIP_STATUS=$(git status --short)
PRE_EXISTING_STASHES=$(git stash list --format='%H')
```

Expected: all ancestry/equality checks pass. If the target moved, return to
Task 3 without touching the worktree.

- [ ] **Step 2: Create and verify the dedicated safety stash**

If `PRE_WIP_STATUS` is non-empty, run:

```bash
git stash push --include-untracked \
  -m 'codex: preserve target WIP before five integration'
SAFETY_STASH_OID=$(git rev-parse refs/stash)

test "$(git rev-parse "$SAFETY_STASH_OID^1")" = "$LAND_BASE_SHA"
test "$(git rev-parse HEAD)" = "$LAND_BASE_SHA"
test -z "$(git status --porcelain)"
```

Expected: the target worktree is clean, ignored files remain present and
untouched, and the new stash's first parent is the captured target.

If `PRE_WIP_STATUS` is empty, set:

```bash
SAFETY_STASH_OID=
```

- [ ] **Step 3: Fast-forward with the final Git ref-lock check**

Run:

```bash
git merge --ff-only "$VERIFIED_CANDIDATE_SHA"
rc=$?
test "$rc" -ne 0 ||
  test "$(git rev-parse HEAD)" = "$VERIFIED_CANDIDATE_SHA"
```

Expected: `rc=0` and `HEAD` equals the verified candidate.

If `rc` is nonzero:

1. do not force-update the branch;
1. restore the safety stash with
   `git stash apply --index "$SAFETY_STASH_OID"` when one exists;
1. verify the restored status exactly as in Step 4;
1. when restoration succeeds, drop only the new stash with Step 5's
   object-ID lookup; and
1. return to Task 3 with the new target tip.

If restoration fails, retain the safety stash and stop.

- [ ] **Step 4: Restore staged, unstaged, and untracked WIP**

When a safety stash exists, run:

```bash
git stash apply --index "$SAFETY_STASH_OID"
rc=$?
POST_WIP_STATUS=$(git status --short)
```

Expected:

- `rc=0`;
- no unmerged paths;
- `POST_WIP_STATUS` has the same path/state entries as `PRE_WIP_STATUS`; and
- ignored local files remain untouched.

Verify:

```bash
test -z "$(git diff --name-only --diff-filter=U)"
test "$POST_WIP_STATUS" = "$PRE_WIP_STATUS"
```

If either check fails, retain the safety stash and source resources, stop
cleanup, and report the exact replay conflict.

- [ ] **Step 5: Drop only the verified new safety stash**

Skip this step when `SAFETY_STASH_OID` is empty. After successful WIP
restoration, resolve the current reflog selector by object ID:

```bash
SAFETY_STASH_REF=$(
  git stash list --format='%H %gd' |
    awk -v oid="$SAFETY_STASH_OID" '$1 == oid { print $2 }'
)

test -n "$SAFETY_STASH_REF"
test "$(git rev-parse "$SAFETY_STASH_REF")" = "$SAFETY_STASH_OID"
git stash drop "$SAFETY_STASH_REF"
test "$(git stash list --format='%H')" = "$PRE_EXISTING_STASHES"
```

Expected: only the workflow-created stash is removed; every pre-existing
stash object remains in the same order.

- [ ] **Step 6: Classify any post-landing HEAD advance**

Run:

```bash
LANDED_SHA="$VERIFIED_CANDIDATE_SHA"
CURRENT_TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")

git merge-base --is-ancestor "$LANDED_SHA" "$CURRENT_TARGET_SHA"
git merge-base --is-ancestor "$SOURCE_SHA" "$CURRENT_TARGET_SHA"
```

Expected: both checks pass.

- If `CURRENT_TARGET_SHA` equals `LANDED_SHA`, the tested merge remains the
  current target.
- If it is a normal descendant, retain it and state that full-gate evidence
  covers `LANDED_SHA`, not the later commit.
- If either ancestry check fails, stop cleanup and retain the source branch
  and source worktree.

______________________________________________________________________

### Task 5: Delete only the integrated source and temporary resources

**Files:**

- Remove worktree:
  `/home/xieshang/Workdir/1.phytomni/Phytomni-Web/.worktrees/Phytomni-Bot-five-sync`.
- Remove worktree: `/tmp/phytomni-bot-five-integration.pZsgRg`.
- Delete local branch: `codex/five-sync-agent-multiturn`.
- Delete local branch: `codex/five-integration`.
- Preserve: `/tmp/phytomni-bot-mcp-gate` registration.

**Interfaces:**

- Consumes: Task 4's landed/source ancestry proof and restored target
  worktree.

- Produces: final local branch/worktree state with no remote changes.

- [ ] **Step 1: Reconfirm cleanup eligibility**

Run from the target worktree:

```bash
cd "$TARGET_WORKTREE"
CURRENT_TARGET_SHA=$(git rev-parse "$TARGET_BRANCH")

git merge-base --is-ancestor "$LANDED_SHA" "$CURRENT_TARGET_SHA"
git merge-base --is-ancestor "$SOURCE_SHA" "$CURRENT_TARGET_SHA"
test -z "$(git -C "$SOURCE_WORKTREE" status --porcelain)"
test -z "$(git -C "$INTEGRATION_WORKTREE" status --porcelain)"
```

Expected: every command succeeds. Any failure blocks deletion.

- [ ] **Step 2: Remove the exact source worktree**

Run:

```bash
git worktree remove "$SOURCE_WORKTREE"
```

Expected: the exact registered Bot source worktree is removed. This command
may require permission because its physical directory is under the sibling
Web checkout. Do not remove any sibling tracked content.

- [ ] **Step 3: Safely delete the integrated source branch**

Run:

```bash
git branch -d "$SOURCE_BRANCH"
```

Expected: Git confirms deletion. Do not substitute `-D`.

- [ ] **Step 4: Remove the exact integration worktree and branch**

Run from the target worktree, never from inside the integration worktree:

```bash
git worktree remove "$INTEGRATION_WORKTREE"
git branch -d "$INTEGRATION_BRANCH"
```

Expected: both operations succeed because the target contains the integration
tip. Do not run `git worktree prune`.

- [ ] **Step 5: Verify final refs, worktrees, WIP, and remote boundary**

Run:

```bash
git branch --all --verbose --no-abbrev
git worktree list --porcelain
git status --short --branch
git merge-base --is-ancestor "$SOURCE_SHA" "$TARGET_BRANCH"
git merge-base --is-ancestor "$LANDED_SHA" "$TARGET_BRANCH"
git log --graph --decorate --oneline -n 20 "$TARGET_BRANCH"
```

Expected:

- neither local `codex/five-sync-agent-multiturn` nor
  `codex/five-integration` exists;

- neither removed worktree path is registered;

- `/tmp/phytomni-bot-mcp-gate` remains untouched;

- the target retains the source and landed merge ancestry;

- the target worktree still shows the restored unrelated WIP;

- no pre-existing stash changed; and

- no remote ref changed.

- [ ] **Step 6: Report the exact verification boundary**

Report:

- source SHA;
- landed merge SHA;
- current target SHA;
- source full-gate summary;
- merged-result full-gate summary and convergence round;
- whether current target equals or descends from the tested merge;
- restored WIP path/state summary;
- deleted local branches and worktrees; and
- explicit confirmation that nothing was pushed and no remote ref changed.
