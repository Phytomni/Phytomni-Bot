# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Supported species_code catalog plus a warn-but-accept resolver guard.

Public: supported_species_codes, warn_if_unsupported_species.

A resolved non-blank ``species_code`` outside the bundled data map is
logged at WARNING but never rejected, mirroring the network resolver's
warn-but-accept handling of upstream-deprecated TO ids (there is no
single canonical code table to hard-reject against).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ...config.data_loaders import load_species_data
from ...config.defaults import PRE_PREPARED_DATA_PATH

__all__ = [
    "supported_species_codes",
    "warn_if_unsupported_species",
]


@lru_cache(maxsize=1)
def supported_species_codes() -> frozenset[str]:
    """Return the species codes present anywhere in the bundled data map.

    Sourced from ``species_data_list.json`` as the union of the
    second-level keys across every ``analysis_type`` (the blank ``""``
    placeholder key is dropped). A species_code absent from every
    analysis cannot be served, so a resolver choosing one signals a
    likely downstream data miss. Cached once per process because the
    bundled data file never changes at runtime.
    """
    data = load_species_data(str(PRE_PREPARED_DATA_PATH))
    codes: set[str] = set()
    for per_species in data.values():
        codes.update(code for code in per_species if code)
    return frozenset(codes)


def warn_if_unsupported_species(
    logger: logging.Logger,
    species_code: str,
    raw_query: str,
) -> None:
    """WARN (do not reject) when a resolved species_code is unknown.

    The resolver still returns the chosen species_code so a narrowly-
    covered or LLM-misformatted value does not hard-fail a request the
    backend might still serve; the WARNING carries the code and the
    original query so operators can triage a likely downstream miss.
    Mirrors ``network.resolve_query._warn_if_deprecated``.
    """
    if species_code in supported_species_codes():
        return
    logger.warning(
        "resolver chose species_code %r not in the supported data map "
        "for query %r; the request proceeds but the downstream analysis "
        "has no data entry for this species",
        species_code,
        raw_query,
    )
