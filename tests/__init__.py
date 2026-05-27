# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Marks ``tests/`` as a regular package.

The disambiguation lets mypy see ``tests/conftest.py`` as
``tests.conftest`` so the root ``conftest.py`` (env install before
``mcp_server_phytomni`` imports) does not collide with it under the
same top-level ``conftest`` module name. The file is intentionally
empty besides this docstring.
"""
