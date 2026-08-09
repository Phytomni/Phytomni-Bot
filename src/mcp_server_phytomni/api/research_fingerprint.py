# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Request-value adapter for the typed Research idempotency fingerprint."""

from __future__ import annotations

import hashlib

from .research_input import (
    ResearchClientFingerprintInput,
    ResearchHttpAdmissionInput,
    compute_research_client_fingerprint,
)

__all__ = [
    "research_client_fingerprint_for_http_input",
]


def research_client_fingerprint_for_http_input(
    request: ResearchHttpAdmissionInput,
    conversation_identity_digest: str | None,
) -> str:
    """Hash one validated HTTP input through the typed contract."""
    query = request.original_query.encode("utf-8")
    return compute_research_client_fingerprint(
        ResearchClientFingerprintInput(
            original_query_digest=hashlib.sha256(query).hexdigest(),
            original_query_length=len(request.original_query),
            managed_asset_ids=request.managed_asset_ids,
            locale=request.locale,
            interop_mode=request.interop_mode,
            interop_targets=request.interop_targets,
            conversation_identity_digest=conversation_identity_digest,
        )
    )
