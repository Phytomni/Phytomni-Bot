# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Static JSON Schema artefacts for the declarative graph loader.

Houses ``graph_manifest.schema.json``, the on-disk export of
``GraphManifest.model_json_schema()`` for external consumers. The
loader test asserts the exported file equals the Pydantic-generated
schema so drift between the source-of-truth model and the on-disk
spec fails the gate at the next push.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent
GRAPH_MANIFEST_SCHEMA_PATH = SCHEMA_DIR / "graph_manifest.schema.json"

__all__ = ["SCHEMA_DIR", "GRAPH_MANIFEST_SCHEMA_PATH"]
