# Five Branch Integration Design

**Status:** Approved in conversation on 2026-07-29

## Goal

Merge the complete history of `codex/five-sync-agent-multiturn` into the
latest local `release/0.1.4`, verify the merged commit with the repository's
authoritative local gate, preserve unrelated staged and unstaged work, and
then delete the source branch and its dedicated worktree.

This is a local integration. It does not push, fetch, delete remote refs, or
modify tracked content or refs in sibling repositories. Cleanup does remove
the registered Bot worktree whose physical directory is under the sibling
Web repository's `.worktrees/` directory.

## Observed Repository State

At the time this design was approved:

- `release/0.1.4` pointed to
  `4f5cde809c52ce40da0f05dbec3db340cdd7e56b`;
- `codex/five-sync-agent-multiturn` pointed to
  `e7a8c887ad255ff865edfb51654023f338e7ea7b`;
- their merge base was
  `ef3ba3cd4ac6f9efdb2a4a98a553dd005ab5a434`;
- the target and source contained 80 and 62 unique commits, respectively;
- the source worktree was clean;
- the source branch had no configured upstream and no visible remote ref; and
- the target worktree contained unrelated uncommitted work.

These values are evidence for the design, not fixed execution inputs. Every
branch tip and worktree status must be resolved again immediately before an
operation that depends on it. A moved target must be incorporated and the
merged result reverified; it must never be overwritten.

## Integration Strategy

Use an isolated integration branch and worktree based on `release/0.1.4`.
Keep the user's primary target worktree untouched while resolving the source
merge and running the full gate.

The integration must preserve both branch histories with a merge commit.
Squashing, rebasing the source branch, and cherry-picking its 62 unique
commits are out of scope because they would discard or rewrite source
provenance.

The integration branch may contain the approved design and implementation
plan commits before the source merge. If `release/0.1.4` advances before the
source merge, replay those integration-only documentation commits onto the
new target tip or otherwise incorporate the new tip without rewriting any
shared branch. After a merged SHA passes the gate, update the target only
while an old-HEAD guard proves that the tested target parent is still current.
If the guard fails, incorporate the new target tip and rerun the gate.

## Moving-Target Convergence Protocol

Treat `release/0.1.4` as a moving target rather than assuming that one
captured HEAD remains current.

For each convergence round:

1. Capture the current target tip as `Tn`.
2. Ensure the isolated candidate contains `Tn`, the source tip, and the
   integration-only documentation commits.
3. Resolve any new semantic conflicts and run the full merged-result gate.
4. Re-read `release/0.1.4` after the gate:
   - if it still equals `Tn`, the candidate is eligible to land;
   - if it advanced normally from `Tn`, merge that new target tip into the
     isolated candidate and repeat the full gate; or
   - if `Tn` is no longer an ancestor of the new tip, treat this as a
     non-fast-forward rewrite, stop, and preserve every integration resource.

Allow at most three full-gate convergence rounds for one integration attempt.
Every rebuilt candidate consumes one round. If the target advances during all
three, or if the final fast-forward check fails after the third, stop before
landing and request a short writer pause rather than claim that an already
stale candidate is current.

Landing uses `git merge --ff-only` from the checked-out target worktree. This
provides a final ref-lock check: if the target moved after the last equality
check and the candidate does not contain the new tip, the command fails
without replacing the branch. Restore the safety stash, incorporate the new
tip in the isolated candidate, and begin another convergence round subject
to the same limit. Never use a force update to win this race.

After a successful landing, record the landed, fully verified merge SHA as
`L` and re-read the target:

- if the target still equals `L`, cleanup may continue;
- if `L` is an ancestor of the new target tip, preserve the newer commits.
  The source remains integrated, so cleanup may continue after proving both
  `L` and the source tip are ancestors. Report clearly that the full-gate
  evidence applies to `L`, not to the later concurrent commits; or
- if `L` is not an ancestor of the new target tip, stop cleanup and retain the
  source branch and worktree for investigation.

This post-landing rule prevents a normal concurrent commit from being lost
while also preventing cleanup after a history rewrite that discarded the
verified merge.

## Pre-Merge Verification

Before creating the source merge commit:

1. Resolve and record the current target SHA, source SHA, merge base, branch
   divergence, worktree registrations, and target worktree status.
2. Confirm that the source worktree is clean.
3. Confirm that the source branch is not already an ancestor of the target.
   If it is already integrated, skip the merge and proceed to final
   verification and cleanup.
4. Run the authoritative full local gate on the clean source tip:

   ```bash
   UV_CACHE_DIR=/tmp/phytomni-five-source-cache \
     ./scripts/validate_local.sh
   ```

5. Stop without merging or deleting anything if the source gate fails.

No existing stash may be popped or dropped. No unrelated worktree may be
removed or pruned.

## Merge and Conflict Resolution

Create a normal merge commit from the source branch into the isolated
integration branch. A dry merge analysis at design time identified eight
textual conflict blocks in five files:

- `src/mcp_server_phytomni/agents/data/agent.py`;
- `src/mcp_server_phytomni/api/app.py`;
- `src/mcp_server_phytomni/mcp/app.py`;
- `tests/server/test_mcp_app_invoke.py`; and
- `tests/server/test_query_route.py`.

The exact conflict set must be recalculated after accounting for any target
advance.

Resolve each conflict semantically. Retain the target branch's newer runtime,
background-submission, routing-evaluation, and static-analysis behavior while
integrating the source branch's conversation-context contracts and
multi-turn behavior. Combine test assertions so each retained behavior has
coverage. Do not resolve an entire conflicted file with blanket `ours` or
`theirs`.

Use CodeGraph before reading or editing the conflicted production symbols.
Run focused tests for every affected production and server-test seam before
the full gate.

## Merged-Result Verification

Verification runs against the isolated committed merge tree, not against the
user's dirty target worktree.

Required checks are:

```bash
git diff --check "$TESTED_TARGET_SHA"...HEAD
UV_CACHE_DIR=/tmp/phytomni-five-merge-cache ./scripts/validate_local.sh
```

`TESTED_TARGET_SHA` is the target tip captured immediately before building
the candidate merge.

Also prove:

- the source tip is an ancestor of the candidate merged SHA;
- the tested target tip is an ancestor of the candidate merged SHA;
- no unresolved index entries or conflict markers remain;
- the source worktree and integration worktree are clean; and
- the current target tip has been re-read and classified by the moving-target
  convergence protocol.

Any gate or ancestry failure stops integration. A normal target advance
starts the next bounded convergence round instead. Preserve the source
branch, source worktree, candidate integration branch, and available evidence
for diagnosis.

## Preserving the Target Worktree

Immediately before advancing `release/0.1.4`:

1. Re-read the target worktree status, including staged, unstaged, and
   untracked paths.
2. Create a clearly named safety stash for those changes, including untracked
   files but excluding ignored local configuration and secrets.
3. Confirm that the stash's first parent matches the captured target HEAD,
   the target HEAD did not move during stashing, the worktree is clean, and
   the safety stash exists.
4. Fast-forward `release/0.1.4` to the verified integration SHA. Do not create
   a second merge commit and do not force-update the branch.
5. Restore the safety stash with its index state.
6. Verify that the intended staged, unstaged, and untracked path states are
   restored.
7. Retain the safety stash until restoration has been checked successfully.
8. After successful restoration verification, drop only the newly created
   safety stash. Match its recorded object ID to the current stash reflog,
   verify the resolved selector still names that object, and then drop that
   selector. Do not assume a fixed `stash@{n}` position and do not touch any
   pre-existing stash.

If stash restoration conflicts, stop cleanup and retain the stash, source
branch, and source worktree. Resolve only the replay of the user's original
changes; do not disguise a failed replay as a successful cleanup.

If the target worktree changes again during the stash-and-land critical
section, stop before cleanup. A continuously active writer requires a short
coordination pause; Git ref locking cannot protect arbitrary filesystem edits
made outside Git.

The authoritative green result applies to the committed merge SHA. Restored
uncommitted work is reported separately and is not included in that gate
claim.

## Source Cleanup

Cleanup is allowed only after the merged SHA is green, the target contains
the source tip, and the target worktree has been restored.

1. Reconfirm that the source worktree is clean.
2. Remove only the registered source worktree:

   ```text
   /home/xieshang/Workdir/1.phytomni/Phytomni-Web/.worktrees/Phytomni-Bot-five-sync
   ```

3. Delete `codex/five-sync-agent-multiturn` with safe branch deletion.
4. Remove the temporary integration worktree and delete its branch only
   after `release/0.1.4` contains the integration tip.
5. Verify that neither deleted branch remains registered in `git branch` or
   `git worktree list`.

Do not run a broad worktree prune. In particular, leave the unrelated stale
`/tmp/phytomni-bot-mcp-gate` registration untouched.

No remote branch is visible and no remote mutation was requested. Therefore
the workflow must not push or issue a remote branch deletion.

## Failure and Rollback Boundaries

- Before the target fast-forward, rollback is simply retaining the isolated
  integration branch and leaving the target unchanged.
- If the target advances before landing, converge on the new tip and rerun
  the full gate; never move the target back to the stale tested SHA.
- If the target advances normally after landing, retain the new descendant
  HEAD and keep the gate claim scoped to the landed merge SHA.
- If the target is rewritten so that it no longer contains the landed merge,
  stop cleanup and retain the source branch and worktree.
- After the target fast-forward, preserve the merge commit and safety stash
  if worktree replay needs repair; do not reset or force-update the target.
- Never delete the source branch merely because a merge commit exists.
  Cleanup requires green verification, ancestry proof, and successful
  worktree restoration.
- Never bypass a failing gate with `--no-verify`, a waiver, or a reduced test
  claim.

## Acceptance Criteria

The integration is complete only when all of the following are true:

- `release/0.1.4` contains both its pre-integration tip and the exact source
  tip as ancestors;
- the full local gate passed on the landed merge SHA, and any later
  concurrent descendant commits are explicitly excluded from that claim;
- all merge conflicts were resolved semantically and no unresolved entries
  remain;
- unrelated target-worktree changes retain their intended staged and
  unstaged state;
- the temporary safety stash was dropped only after successful restoration,
  while every pre-existing stash remains unchanged;
- the source worktree is removed;
- the local `codex/five-sync-agent-multiturn` branch is deleted safely;
- temporary integration resources created for this workflow are removed;
- unrelated worktrees, pre-existing stashes, ignored local files, and sibling
  repository tracked contents and refs remain untouched; and
- no remote refs were changed.
