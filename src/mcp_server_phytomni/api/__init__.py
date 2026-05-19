# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""External HTTP API package for Phytomni agents.

Exposes a FastAPI application that fronts the existing MCP agent layer over
authenticated REST endpoints. Public symbols are re-exported incrementally
as API layers land so every commit stays import-clean.
"""
