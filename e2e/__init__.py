# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Package marker for the live business E2E suite.

Pytest discovers ``e2e/conftest.py`` and ``e2e/test_*_e2e.py`` modules
which use intra-package relative imports such as ``from .helpers.client
import make_client``. Those relative imports require the directory to
be a real Python package, so this empty marker file establishes the
``e2e`` package context. The directory is intentionally excluded from
the wheel build (``[tool.hatch.build.targets.wheel].packages`` lists
only the ``src/`` packages) and from default pytest discovery (root
``testpaths = ["tests"]``).
"""
