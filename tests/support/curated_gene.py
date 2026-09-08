# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Neutral curated-material fixture data, independent of production models."""


def protocol_association() -> dict[str, str]:
    """Return fresh display metadata for the explicitly approved protocol."""
    return {
        "id": "protocol",
        "name": "protocol.md",
        "kind": "markdown",
        "markdown_href": "./protocol.md",
        "media_type": "text/markdown",
    }
