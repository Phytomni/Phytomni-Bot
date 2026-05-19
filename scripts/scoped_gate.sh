#!/usr/bin/env sh
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

# ---------------------------------------------------------------------------
# Scoped quality gate ("区域门禁").
#
# Runs the same tools/flags as scripts/validate_local.sh, but only over the
# files in the active change region, so parallel dev agents are not blocked by
# a full-tree pre-push. Default CI / other agents are unaffected: the pre-push
# hook still runs validate_local.sh by default and only delegates here when
# PHYTOMNI_SCOPED_GATE=1.
#
# Usage: scripts/scoped_gate.sh <precommit|prepush|scoped>
#   precommit  scope = staged index (matches pre-commit timing)
#   prepush    scope = BASE..work-tree (BASE = @{upstream} else merge-base main)
#   scoped     alias of prepush
# ---------------------------------------------------------------------------

mode=${1:-prepush}
case "$mode" in
    precommit | prepush | scoped) ;;
    *)
        printf 'usage: scripts/scoped_gate.sh <precommit|prepush|scoped>\n' >&2
        exit 2
        ;;
esac
if [ "$mode" = scoped ]; then
    mode=prepush
fi

run() {
    printf '\n==> %s\n' "$*"
    "$@"
}

# ---------------------------------------------------------------------------
# Resolve the change region and the changed-file set.
#   precommit : staged index only.
#   prepush   : BASE = @{upstream} if it resolves, else merge-base HEAD main,
#               UNION untracked-not-ignored files.
# ---------------------------------------------------------------------------
BASE=""
if [ "$mode" = precommit ]; then
    changed=$(git diff --cached --name-only --diff-filter=ACMR)
else
    if BASE=$(git rev-parse --verify --quiet '@{upstream}'); then
        :
    elif BASE=$(git merge-base HEAD main 2>/dev/null); then
        :
    else
        printf '%s\n' \
            'scoped_gate: cannot resolve @{upstream} or merge-base HEAD main;' \
            'scoped_gate: nothing to compare against, run "make full" instead.' \
            >&2
        exit 1
    fi
    tracked=$(git diff --name-only --diff-filter=ACMR "$BASE")
    untracked=$(git ls-files --others --exclude-standard)
    changed=$(printf '%s\n%s\n' "$tracked" "$untracked" |
        sort -u | sed '/^[[:space:]]*$/d')
fi

if [ "$mode" = prepush ]; then
    printf '==> scoped gate (prepush) BASE=%s\n' "$BASE"
else
    printf '==> scoped gate (precommit, staged)\n'
fi

# ---------------------------------------------------------------------------
# Partition the change set into per-kind newline lists. An empty list means
# the corresponding step is skipped cleanly, so a linter is NEVER invoked with
# an empty argument vector (which would make it lint the whole tree or error).
# ---------------------------------------------------------------------------
py_files=""
yaml_files=""
json_files=""
test_files=""
demo_changed=0

old_ifs=$IFS
IFS='
'
for f in $changed; do
    if [ -z "$f" ]; then
        continue
    fi
    # ACMR keeps the new path on rename and excludes deletes; a path can still
    # vanish in a racing worktree, so drop anything no longer on disk.
    if [ ! -e "$f" ]; then
        continue
    fi
    case "$f" in
        demo_data/*)
            demo_changed=1
            ;;
    esac
    case "$f" in
        *.py)
            py_files="${py_files}${f}
"
            case "$f" in
                tests/*test_*.py)
                    test_files="${test_files}${f}
"
                    ;;
            esac
            ;;
        *.yaml | *.yml)
            yaml_files="${yaml_files}${f}
"
            ;;
        *.json)
            json_files="${json_files}${f}
"
            ;;
    esac
done
IFS=$old_ifs

py_files=$(printf '%s' "$py_files" | sed '/^[[:space:]]*$/d')
yaml_files=$(printf '%s' "$yaml_files" | sed '/^[[:space:]]*$/d')
json_files=$(printf '%s' "$json_files" | sed '/^[[:space:]]*$/d')
test_files=$(printf '%s' "$test_files" | sed '/^[[:space:]]*$/d')

# ---------------------------------------------------------------------------
# scan_secrets — scoped variant per mode (mirrors validate_local.sh tool).
# ---------------------------------------------------------------------------
if [ "$mode" = precommit ]; then
    run python3 scripts/scan_secrets.py --staged
else
    run python3 scripts/scan_secrets.py --git-range "$BASE..HEAD"
fi

# ---------------------------------------------------------------------------
# whitespace check (cheap, always; mirrors validate_local.sh).
# ---------------------------------------------------------------------------
if [ "$mode" = precommit ]; then
    run git diff --cached --check
else
    run git diff --check
fi

# ---------------------------------------------------------------------------
# Python toolchain — only when .py files changed. $@ is rebuilt at the top
# level (a function-local `set --` would not survive the return), then reused
# across the consecutive Python tools.
# ---------------------------------------------------------------------------
if [ -z "$py_files" ]; then
    printf '\n==> %s\n' \
        'no changed .py files; skipping compileall/black/ruff/flake8/mypy/pyright/pylint'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $py_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs

    run uv run python -m compileall "$@"
    run uv run black --check "$@"
    run uv run ruff check "$@"
    run uv run flake8 "$@"
    run uv run mypy "$@"

    if command -v pyright >/dev/null 2>&1; then
        run pyright "$@"
    else
        run npx --yes pyright "$@"
    fi

    # NOTE: validate_local.sh does NOT set PYTHONPATH for pylint; mirror that.
    run uv run pylint --persistent=no "$@"
fi

# ---------------------------------------------------------------------------
# yamllint — only changed YAML.
# ---------------------------------------------------------------------------
if [ -z "$yaml_files" ]; then
    printf '\n==> no changed yaml files; skipping yamllint\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $yaml_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run yamllint "$@"
fi

# ---------------------------------------------------------------------------
# jsonlint — per changed JSON file (mirrors validate_local.sh loop + fallback).
# ---------------------------------------------------------------------------
if command -v jsonlint >/dev/null 2>&1; then
    jsonlint_cmd() { jsonlint "$1" --quiet; }
else
    jsonlint_cmd() { npx --yes jsonlint "$1" --quiet; }
fi

if [ -z "$json_files" ]; then
    printf '\n==> no changed json files; skipping jsonlint\n'
else
    printf '\n==> jsonlint (per JSON file)\n'
    old_ifs=$IFS
    IFS='
'
    for file in $json_files; do
        if [ -n "$file" ]; then
            jsonlint_cmd "$file"
        fi
    done
    IFS=$old_ifs
fi

# ---------------------------------------------------------------------------
# demo_data idempotency — ONLY when a changed path is under demo_data/. A
# concurrent agent may own demo_data/; never regenerate foreign scratch.
# ---------------------------------------------------------------------------
if [ "$demo_changed" -eq 1 ]; then
    printf '\n==> demo_data idempotency check\n'
    uv run python demo_data/scripts/generate_demo_data.py
    if ! git diff --quiet -- demo_data/; then
        printf 'demo_data/ drifted after running the generator:\n' >&2
        git --no-pager diff --stat -- demo_data/ >&2
        exit 1
    fi
else
    printf '\n==> demo_data/ not in change set; skipping demo_data idempotency\n'
fi

# ---------------------------------------------------------------------------
# pytest — scoped: changed tests/**/test_*.py PLUS the 3 structural tests
# whenever any .py changed (they scan all tracked .py, so a non-test .py change
# can still break them). Nothing to run when no .py changed. e2e/ is never
# added (root pyproject testpaths already excludes it).
# ---------------------------------------------------------------------------
if [ -z "$py_files" ]; then
    printf '\n==> no changed .py files; skipping pytest\n'
else
    pytest_targets=$(
        {
            printf '%s\n' "$test_files"
            printf '%s\n' tests/unit/test_style_naming.py
            printf '%s\n' tests/unit/test_pytest_layers.py
            printf '%s\n' tests/unit/test_package_boundaries.py
        } | sed '/^[[:space:]]*$/d' | sort -u
    )
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $pytest_targets; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run pytest "$@"
fi

printf '\n==> scoped gate passed\n'
