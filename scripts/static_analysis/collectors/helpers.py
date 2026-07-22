# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Shared finding construction for policy collectors."""

from __future__ import annotations

from ..fingerprints import Endpoint, finding_fingerprint
from ..model import Finding, FindingParts


def make_finding(parts: FindingParts) -> Finding:
    """Construct a finding and its content-addressed identity."""
    endpoint = Endpoint(parts["path"], parts["symbol"], parts["source"])
    return Finding(
        tool=parts["tool"],
        rule=parts["rule"],
        mechanism=parts["mechanism"],
        target_kind=parts["target_kind"],
        path=parts["path"],
        symbol=parts["symbol"],
        peer_path=parts["peer_path"],
        peer_symbol=parts["peer_symbol"],
        fingerprint=finding_fingerprint(
            parts["tool"],
            parts["rule"],
            parts["mechanism"],
            (endpoint,),
            parts["source"],
        ),
        location=parts["location"],
        message=parts["message"],
        tool_version=None,
    )
