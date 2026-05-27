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
sh_files=""
md_files=""
toml_files=""
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
    *.sh | .githooks/*)
        sh_files="${sh_files}${f}
"
        ;;
    *.md)
        # demo_data/*.md is generator-owned (covered by the demo_data
        # idempotency check); never hand-format it.
        case "$f" in
        demo_data/*) ;;
        *)
            md_files="${md_files}${f}
"
            ;;
        esac
        ;;
    *.toml)
        toml_files="${toml_files}${f}
"
        ;;
    esac
done
IFS=$old_ifs

py_files=$(printf '%s' "$py_files" | sed '/^[[:space:]]*$/d')
yaml_files=$(printf '%s' "$yaml_files" | sed '/^[[:space:]]*$/d')
json_files=$(printf '%s' "$json_files" | sed '/^[[:space:]]*$/d')
sh_files=$(printf '%s' "$sh_files" | sed '/^[[:space:]]*$/d')
md_files=$(printf '%s' "$md_files" | sed '/^[[:space:]]*$/d')
toml_files=$(printf '%s' "$toml_files" | sed '/^[[:space:]]*$/d')
test_files=$(printf '%s' "$test_files" | sed '/^[[:space:]]*$/d')

# Workflow files are a strict subset of yaml_files (still linted by yamllint
# unchanged); the subset is what actionlint targets. Filter once here so the
# step below stays empty-skip-clean when no workflow changed.
workflow_files=""
old_ifs=$IFS
IFS='
'
for f in $yaml_files; do
    case "$f" in
    .github/workflows/*) workflow_files="${workflow_files}${f}
" ;;
    esac
done
IFS=$old_ifs
workflow_files=$(printf '%s' "$workflow_files" | sed '/^[[:space:]]*$/d')

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
    run uv run pyright "$@"

    # NOTE: validate_local.sh does NOT set PYTHONPATH for pylint; mirror that.
    # --disable=R0801,R0903 mirrors validate_local.sh: those rules are
    # tracked by scripts/check_pylint_baseline.py on the full gate, not
    # at the scoped gate (subset runs would emit fragmentary counts).
    run uv run pylint --persistent=no --disable=R0801,R0903 "$@"
fi

# ---------------------------------------------------------------------------
# Static + format check of changed shell scripts (incl. .githooks/* hooks).
# ---------------------------------------------------------------------------
if [ -z "$sh_files" ]; then
    printf '\n==> no changed shell files; skipping shellcheck/shfmt\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $sh_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run shellcheck "$@"
    run scripts/shfmt_runner.sh -d -i 4 "$@"
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
# actionlint — only changed .github/workflows/* (a subset of yamllint above).
# yamllint covers YAML shape; actionlint adds workflow-semantic checks
# (action versions, required inputs, shell errors in run: blocks).
# Resolved via scripts/actionlint_runner.sh — mirrors shfmt_runner.sh:
# on-PATH binary, else a pinned go-installed binary cached under
# .cache/phytomni/, else fail with the official install hint.
# ---------------------------------------------------------------------------
if [ -z "$workflow_files" ]; then
    printf '\n==> no changed workflow files; skipping actionlint\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $workflow_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run scripts/actionlint_runner.sh "$@"
fi

# ---------------------------------------------------------------------------
# Markdown — only changed *.md (demo_data/*.md is excluded in the partition
# above: it is generator-owned and covered by the demo_data idempotency
# check). mdformat owns line shape (--wrap keep); pymarkdown reads the
# [tool.pymarkdown] rule config from pyproject.toml.
# ---------------------------------------------------------------------------
if [ -z "$md_files" ]; then
    printf '\n==> no changed markdown files; skipping mdformat/pymarkdown\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $md_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run mdformat --check "$@"
    run uv run pymarkdown --config pyproject.toml scan "$@"
fi

# ---------------------------------------------------------------------------
# TOML — only changed *.toml. toml-sort reads [tool.tomlsort] from
# pyproject.toml so the CLI takes no flags (one source of truth shared
# with CI). validate-pyproject checks PEP 621 schemas and is scoped by
# basename to *pyproject.toml — running it on a non-pyproject TOML would
# fail by design, since the schema does not apply.
# ---------------------------------------------------------------------------
if [ -z "$toml_files" ]; then
    printf '\n==> no changed toml files; skipping toml-sort/validate-pyproject\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $toml_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run toml-sort --check "$@"

    set --
    old_ifs=$IFS
    IFS='
'
    for f in $toml_files; do
        case "$(basename "$f")" in
        pyproject.toml) set -- "$@" "$f" ;;
        esac
    done
    IFS=$old_ifs
    if [ "$#" -gt 0 ]; then
        run uv run validate-pyproject "$@"
    else
        printf '\n==> no changed pyproject.toml; skipping validate-pyproject\n'
    fi
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
# normalize_json --check — format-side of the JSON gate, scoped to the
# config JSON files normalize_json.py owns. demo_data/*.json is generator-
# owned (covered by the demo_data idempotency check below), so the filter
# here is src/mcp_server_phytomni/config/*.json only.
# ---------------------------------------------------------------------------
config_json_files=""
old_ifs=$IFS
IFS='
'
for f in $json_files; do
    case "$f" in
    src/mcp_server_phytomni/config/*.json) config_json_files="${config_json_files}${f}
" ;;
    esac
done
IFS=$old_ifs
config_json_files=$(printf '%s' "$config_json_files" | sed '/^[[:space:]]*$/d')

if [ -z "$config_json_files" ]; then
    printf '\n==> no changed config json files; skipping normalize_json --check\n'
else
    set --
    old_ifs=$IFS
    IFS='
'
    for f in $config_json_files; do
        if [ -n "$f" ]; then
            set -- "$@" "$f"
        fi
    done
    IFS=$old_ifs
    run uv run python scripts/normalize_json.py --check "$@"
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
