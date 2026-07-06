# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Phytomni MCP server package."""

import contextlib

from .config.settings import load_env_file

# Populate os.environ from the plaintext .env (or decrypt the encrypted
# envelope) before any submodule constructs a ServerConfig subclass at
# import time. Those config models read only os.environ for their
# required deployment endpoints, so the load must run first — otherwise
# importing any agent package raises ValidationError. Under
# PHYTOMNI_TESTING=1 this returns immediately (tests inject their own
# env), so it is a no-op for the test suite.
# No on-disk config source (no plaintext .env, no encrypted envelope)
# raises RuntimeError. Either the environment was supplied directly
# (e.g. docker -e) or an auxiliary entry point needs no secrets (e.g.
# the phytomni-cache CLI over local SQLite). The authoritative "refuse
# to boot with empty secrets" guard stays at the secret-loading seam
# (get_sensitive_config), which re-runs this same check when an agent
# actually needs credentials.
with contextlib.suppress(RuntimeError):
    load_env_file()

# The package re-exports nothing: the import above is a startup
# bootstrap, not a public API surface. Declared explicitly so the
# __init__ re-export check stays satisfied.
__all__ = []
