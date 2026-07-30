# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Cycle-safe access to Review conversation classes."""

from __future__ import annotations

import sys
from typing import Any


def review_classes() -> tuple[type[Any], ...]:
    """Return parent classes after the conversation facade is initialized."""
    module = sys.modules[f"{__package__}.conversation"]
    return (
        module.ReviewCheckpointSnapshot,
        module.ReviewClarificationError,
        module.ReviewConversationOperation,
        module.ReviewReportDocument,
        module.ReviewSection,
        module.RevisedSection,
        getattr(module, "_RestoredReviewSettlement"),
        getattr(module, "_RestoredReviewCheckpoint"),
    )
