#!/usr/bin/env sh
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

# ---------------------------------------------------------------------------
# shfmt resolver. shfmt has no pip/npx package; its official install is
# `go install mvdan.cc/sh/v3/cmd/shfmt@<ver>`. This is the detect-or-install
# equivalent of the command-v-or-npx fallback used for pyright and jsonlint.
# Resolution order:
#   1. shfmt already on PATH                 -> use it
#   2. pinned binary previously go-installed -> use it
#   3. `go` available -> `go install` the pinned version into the gitignored
#      .cache/phytomni/ once (cached after), then use it
#   4. otherwise -> fail with the official install hint
#
# The version is PINNED (not @latest): a format gate must be deterministic, or
# different shfmt releases reformat differently and the gate flaps between
# local and CI. Arguments are forwarded verbatim, e.g.:
#   scripts/shfmt_runner.sh -d -i 4 path/to/script.sh
# ---------------------------------------------------------------------------

SHFMT_VERSION="v3.10.0"
cache_root="$repo_root/.cache/phytomni/shfmt-$SHFMT_VERSION"
cached_bin="$cache_root/shfmt"

if command -v shfmt >/dev/null 2>&1; then
    exec shfmt "$@"
fi

if [ -x "$cached_bin" ]; then
    exec "$cached_bin" "$@"
fi

if command -v go >/dev/null 2>&1; then
    mkdir -p "$cache_root"
    GOBIN="$cache_root" go install \
        "mvdan.cc/sh/v3/cmd/shfmt@$SHFMT_VERSION"
    exec "$cached_bin" "$@"
fi

printf 'shfmt_runner: shfmt not found and go is unavailable.\n' >&2
printf 'shfmt_runner: install it with:\n' >&2
printf '  go install mvdan.cc/sh/v3/cmd/shfmt@%s\n' "$SHFMT_VERSION" >&2
exit 1
