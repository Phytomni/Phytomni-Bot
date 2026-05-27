# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Marks ``tests/agents/`` as a regular package.

Pairs with ``tests/__init__.py`` so sibling-helper modules such as
``tests/agents/_analyst_fakes.py`` import as
``tests.agents._analyst_fakes`` rather than a top-level
``_analyst_fakes`` that mypy cannot resolve under
``explicit_package_bases``.
"""
