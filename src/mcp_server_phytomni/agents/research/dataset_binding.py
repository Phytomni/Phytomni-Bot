# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Subset a Research child data_list from optional cited dataset ids."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["bound_child_data_list"]


def bound_child_data_list(
    parent: Mapping[str, str],
    authorities: Iterable[object],
    dataset_ids: object,
) -> dict[str, str]:
    """Return a child data map bound to cited inventory references."""
    snapshot = dict(parent)
    if dataset_ids is None or not isinstance(dataset_ids, tuple):
        return snapshot
    if not dataset_ids:
        return {}
    id_to_ref = _id_to_ref(authorities)
    wanted = {
        id_to_ref[dataset_id]
        for dataset_id in dataset_ids
        if dataset_id in id_to_ref
    }
    if not wanted:
        return snapshot
    return {
        reference: description
        for reference, description in snapshot.items()
        if reference in wanted
    }


def _id_to_ref(authorities: Iterable[object]) -> dict[str, str]:
    """Map usable authority dataset ids to exact references."""
    id_to_ref: dict[str, str] = {}
    for authority in authorities:
        dataset_id = getattr(authority, "dataset_id", None)
        exact_reference = getattr(authority, "exact_reference", None)
        if not isinstance(dataset_id, str) or not dataset_id:
            continue
        if not isinstance(exact_reference, str) or not exact_reference:
            continue
        id_to_ref[dataset_id] = exact_reference
    return id_to_ref
