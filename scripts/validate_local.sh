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
run uv run python -m compileall src tests scripts/scan_secrets.py
run git diff --check
run git diff --cached --check
run uv run black --check .
run uv run ruff check .
run uv run flake8 src tests
run uv run mypy src

if command -v pyright >/dev/null 2>&1; then
    run pyright src
else
    run npx --yes pyright src
fi

run uv run pylint --persistent=no $(git ls-files '*.py')
run uv run yamllint .
run uv run pytest
