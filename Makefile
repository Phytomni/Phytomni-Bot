# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)

# Scoped quality gate ("区域门禁") + safe push wrapper.
#
# Every gate target is a thin wrapper that execs scripts/scoped_gate.sh, so
# the opt-in pre-push hook and Make share one source of truth (mirroring how
# .githooks/pre-push execs scripts/validate_local.sh today).

SHELL := /bin/sh

# Push must keep the SSH socket alive: the slow pre-push hook idles the
# connection. Use a keepalive instead of --no-verify so the hook still runs.
GIT_SSH_SOCK := /tmp/phytomni-git-github.sock
GIT_SSH_KEEPALIVE := ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=120 -o TCPKeepAlive=yes -o ControlMaster=auto -o ControlPath=$(GIT_SSH_SOCK) -o ControlPersist=1800

.DEFAULT_GOAL := help
.PHONY: help commit-check scoped precommit prepush full push

help:
	@printf 'Phytomni-Bot quality gate targets:\n\n'
	@printf '  make commit-check  validate unpublished commit messages\n'
	@printf '  make precommit   scoped gate over the STAGED index\n'
	@printf '  make prepush     scoped gate over BASE..work-tree\n'
	@printf '  make scoped      alias of prepush (range scope)\n'
	@printf '  make full        full validate_local.sh (CI parity, no scoping)\n'
	@printf '  make push        git push with SSH keepalive (gate still runs)\n'
	@printf '  make help        this message\n\n'
	@printf 'Fast opt-in pre-push:  PHYTOMNI_SCOPED_GATE=1 git push\n'
	@printf 'Combine both:          PHYTOMNI_SCOPED_GATE=1 make push\n'

commit-check:
	@python3 scripts/validate_commit_messages.py --not-on-remotes HEAD

scoped:
	@scripts/scoped_gate.sh scoped

precommit:
	@scripts/scoped_gate.sh precommit

prepush:
	@scripts/scoped_gate.sh prepush

full:
	@scripts/validate_local.sh

push:
	@GIT_SSH_COMMAND='$(GIT_SSH_KEEPALIVE)' git push $(ARGS)
