#!/usr/bin/env sh
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

# ---------------------------------------------------------------------------
# checkmake resolver. checkmake has no pip or npx package; its official
# install is `go install github.com/mrtazz/checkmake/cmd/checkmake@<ver>`.
# This is the detect-or-install equivalent of the command-v-or-npx fallback
# used for pyright and jsonlint, and mirrors scripts/shfmt_runner.sh and
# scripts/actionlint_runner.sh exactly so local and CI run from the same
# pinned binary path.
#
# Resolution order:
#   1. checkmake already on PATH             -> use it
#   2. pinned binary previously go-installed -> use it
#   3. `go` available -> `go install` the pinned version into the gitignored
#      .cache/phytomni/ once (cached after), then use it
#   4. otherwise -> fail with the official install hint
#
# The version is PINNED (not @latest): a Makefile lint gate must be
# deterministic, or different checkmake releases would flap between local
# and CI as new rules are added. Arguments are forwarded verbatim, e.g.:
#   scripts/checkmake_runner.sh Makefile
# ---------------------------------------------------------------------------

CHECKMAKE_VERSION="0.2.2"
cache_root="$repo_root/.cache/phytomni/checkmake-v$CHECKMAKE_VERSION"
cached_bin="$cache_root/checkmake"

if command -v checkmake >/dev/null 2>&1; then
    exec checkmake "$@"
fi

if [ -x "$cached_bin" ]; then
    exec "$cached_bin" "$@"
fi

if command -v go >/dev/null 2>&1; then
    mkdir -p "$cache_root"
    GOBIN="$cache_root" go install \
        "github.com/mrtazz/checkmake/cmd/checkmake@$CHECKMAKE_VERSION"
    exec "$cached_bin" "$@"
fi

printf 'checkmake_runner: checkmake not found and go is unavailable.\n' >&2
printf 'checkmake_runner: install it with:\n' >&2
printf '  go install github.com/mrtazz/checkmake/cmd/checkmake@%s\n' \
    "$CHECKMAKE_VERSION" >&2
exit 1
