# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable output layout for result-delivery child submissions."""

from __future__ import annotations

import re

from ..public_agent_catalog import result_delivery_agent_slugs

RESULT_DELIVERY_AGENTS = result_delivery_agent_slugs()
_CHILD_SEGMENT = re.compile(r"^part-(?:00[1-9]|0[1-9][0-9]|1[0-9]{2})$")


def result_child_output_dir(run_root: str, child_index: int) -> str:
    """Return the stable one-based child directory below one run root."""
    root = run_root.rstrip("/")
    if not root or child_index < 0 or child_index >= 199:
        raise ValueError("invalid result child layout")
    return f"{root}/children/part-{child_index + 1:03d}"


def result_run_root_from_child(child_dir: str) -> str:
    """Recover the umbrella root from one validated result child path."""
    candidate = child_dir.rstrip("/\\")
    for marker in ("/children/", "\\children\\"):
        root, matched, part = candidate.rpartition(marker)
        if matched and root and _CHILD_SEGMENT.fullmatch(part) is not None:
            return root
    raise ValueError("output directory is not a result child")


def is_unallocated_default_output_dir(output_dir: str, default: str) -> bool:
    """Return True when ``output_dir`` is the shared config dump.

    Analyst HTTP and MCP entry points historically seed
    ``AnalystConfig.OUTPUT_DIR`` (a shared test placeholder) as if it
    were a caller-allocated run root. Reusing that prefix makes harvest
    list leftover objects from every prior job that wrote there.

    The default itself and any descendant under it are unallocated.
    Caller-owned paths outside that prefix stay reusable.
    """
    default_root = default.rstrip("/")
    candidate = output_dir.rstrip("/")
    if not default_root or not candidate:
        return False
    if candidate == default_root:
        return True
    return candidate.startswith(f"{default_root}/")


_LEGACY_SHARED_OUTPUT = re.compile(
    r"/agent_data/shared/[0-9a-f]{64}/output(?:/|$)"
)


def is_legacy_shared_output_dir(output_dir: str) -> bool:
    """Return True for the old shared fingerprint dump without ``/jobs/``.

    Those prefixes collected every public-data hit on the same digest.
    Isolated job dirs live at ``shared/<fp>/jobs/<job-id>/`` and stay
    reusable.
    """
    candidate = output_dir.replace("\\", "/").rstrip("/")
    if not candidate or "/jobs/" in candidate:
        return False
    return _LEGACY_SHARED_OUTPUT.search(candidate) is not None


def reusable_caller_output_dir(output_dir: str, default: str) -> str:
    """Return ``output_dir`` only when it is a caller-owned run root.

    Empty input, the shared config dump (and any descendant), and the
    pre-jobs fingerprint dump are treated as unallocated so callers mint
    a unique directory instead of harvesting leftover objects.
    """
    candidate = str(output_dir or "").strip()
    if not candidate:
        return ""
    if is_unallocated_default_output_dir(candidate, default):
        return ""
    if is_legacy_shared_output_dir(candidate):
        return ""
    return candidate
