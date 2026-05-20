#!/usr/bin/env sh
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

# ---------------------------------------------------------------------------
# actionlint resolver. actionlint has no pip/npx package; its official
# install is `go install github.com/rhysd/actionlint/cmd/actionlint@<ver>`.
# This is the detect-or-install equivalent of the command-v-or-npx fallback
# used for pyright and jsonlint, and mirrors scripts/shfmt_runner.sh exactly
# so local and CI run from the same pinned binary path.
#
# Resolution order:
#   1. actionlint already on PATH            -> use it
#   2. pinned binary previously go-installed -> use it
#   3. `go` available -> `go install` the pinned version into the gitignored
#      .cache/phytomni/ once (cached after), then use it
#   4. otherwise -> fail with the official install hint
#
# The version is PINNED (not @latest): a workflow gate must be deterministic,
# or different actionlint releases would flap between local and CI as new
# rules and shellcheck integrations are added. Arguments are forwarded
# verbatim, e.g.:
#   scripts/actionlint_runner.sh -color .github/workflows/lint.yml
# ---------------------------------------------------------------------------

ACTIONLINT_VERSION="v1.7.4"
cache_root="$repo_root/.cache/phytomni/actionlint-$ACTIONLINT_VERSION"
cached_bin="$cache_root/actionlint"

if command -v actionlint >/dev/null 2>&1; then
    exec actionlint "$@"
fi

if [ -x "$cached_bin" ]; then
    exec "$cached_bin" "$@"
fi

if command -v go >/dev/null 2>&1; then
    mkdir -p "$cache_root"
    GOBIN="$cache_root" go install \
        "github.com/rhysd/actionlint/cmd/actionlint@$ACTIONLINT_VERSION"
    exec "$cached_bin" "$@"
fi

printf 'actionlint_runner: actionlint not found and go is unavailable.\n' >&2
printf 'actionlint_runner: install it with:\n' >&2
printf '  go install github.com/rhysd/actionlint/cmd/actionlint@%s\n' \
    "$ACTIONLINT_VERSION" >&2
exit 1
