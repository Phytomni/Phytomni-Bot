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

if command -v jsonlint >/dev/null 2>&1; then
    jsonlint_cmd() { jsonlint "$1" --quiet; }
else
    jsonlint_cmd() { npx --yes jsonlint "$1" --quiet; }
fi

printf '\n==> jsonlint (per JSON file)\n'
git ls-files '*.json' | while IFS= read -r file; do
    jsonlint_cmd "$file"
done

printf '\n==> demo_data idempotency check\n'
uv run python demo_data/scripts/generate_demo_data.py
if ! git diff --quiet -- demo_data/; then
    printf 'demo_data/ drifted after running the generator:\n' >&2
    git --no-pager diff --stat -- demo_data/ >&2
    exit 1
fi

run uv run pytest
