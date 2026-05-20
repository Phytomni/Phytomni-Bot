#!/usr/bin/env sh
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

run() {
    printf '\n==> %s\n' "$*"
    "$@"
}

run python3 scripts/scan_secrets.py --all
run uv run python -m compileall src tests e2e scripts
run git diff --check
run git diff --cached --check
run uv run black --check .
run uv run ruff check .
run uv run flake8 src tests e2e scripts
run uv run mypy src tests e2e scripts
run uv run pyright src tests e2e scripts

set --
while IFS= read -r pyfile; do
    if [ -n "$pyfile" ]; then
        set -- "$@" "$pyfile"
    fi
done <<EOF
$(git ls-files '*.py')
EOF
run uv run pylint --persistent=no "$@"

set --
while IFS= read -r shfile; do
    if [ -n "$shfile" ]; then
        set -- "$@" "$shfile"
    fi
done <<EOF
$(git ls-files '*.sh')
$(git ls-files .githooks)
EOF
run uv run shellcheck "$@"
run scripts/shfmt_runner.sh -d -i 4 "$@"

run uv run yamllint .

# actionlint adds workflow-semantic checks (action versions, required
# inputs, shell errors in run: blocks) on top of yamllint's YAML-shape
# coverage; the runner is the same pinned go-installed binary CI uses.
set --
while IFS= read -r wffile; do
    if [ -n "$wffile" ]; then
        set -- "$@" "$wffile"
    fi
done <<EOF
$(git ls-files '.github/workflows/*')
EOF
run scripts/actionlint_runner.sh "$@"

# demo_data/*.md is generator-owned (covered by the demo_data idempotency
# check below); exclude it so the gate never expects hand-formatted fixtures.
set --
while IFS= read -r mdfile; do
    if [ -n "$mdfile" ]; then
        set -- "$@" "$mdfile"
    fi
done <<EOF
$(git ls-files '*.md' ':!:demo_data/')
EOF
run uv run mdformat --check "$@"
run uv run pymarkdown --config pyproject.toml scan "$@"

# toml-sort owns formatting (reads [tool.tomlsort] from pyproject.toml, no
# CLI flags); validate-pyproject is schema-scoped to pyproject.toml files.
set --
while IFS= read -r tomlfile; do
    if [ -n "$tomlfile" ]; then
        set -- "$@" "$tomlfile"
    fi
done <<EOF
$(git ls-files '*.toml')
EOF
run uv run toml-sort --check "$@"

set --
while IFS= read -r ppfile; do
    if [ -n "$ppfile" ]; then
        set -- "$@" "$ppfile"
    fi
done <<EOF
$(git ls-files '*pyproject.toml')
EOF
run uv run validate-pyproject "$@"

if command -v jsonlint >/dev/null 2>&1; then
    jsonlint_cmd() { jsonlint "$1" --quiet; }
else
    jsonlint_cmd() { npx --yes jsonlint "$1" --quiet; }
fi

printf '\n==> jsonlint (per JSON file)\n'
git ls-files '*.json' | while IFS= read -r file; do
    jsonlint_cmd "$file"
done

# normalize_json --check is the format-side of the JSON gate. It is scoped
# to src/mcp_server_phytomni/config/*.json (the files normalize_json.py
# owns); demo_data/*.json is generator-owned and covered by the demo_data
# idempotency check below.
run uv run python scripts/normalize_json.py --check

printf '\n==> demo_data idempotency check\n'
uv run python demo_data/scripts/generate_demo_data.py
if ! git diff --quiet -- demo_data/; then
    printf 'demo_data/ drifted after running the generator:\n' >&2
    git --no-pager diff --stat -- demo_data/ >&2
    exit 1
fi

run uv run pytest
