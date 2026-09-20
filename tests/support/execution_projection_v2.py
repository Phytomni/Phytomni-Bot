# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared payload construction for Execution V2 projection tests."""

from __future__ import annotations

import hashlib


def execution_message_payload(
    text: str,
    *,
    output_revision: int,
    message_id: str,
) -> dict[str, object]:
    """Build one complete single-chunk assistant message payload."""
    return {
        "output_revision": output_revision,
        "message_id": message_id,
        "source_message_id": message_id,
        "base_offset": 0,
        "offset": len(text),
        "total_length": len(text),
        "chunk_index": 0,
        "chunk_count": 1,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }
