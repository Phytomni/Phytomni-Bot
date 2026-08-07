# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Pure citation-record and DOI normalization contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import quote, unquote, urlsplit

CITATION_RECORD_FIELDS: tuple[str, ...] = (
    "au",
    "ti",
    "so",
    "vl",
    "bp",
    "ep",
    "ar",
    "py",
    "di",
    "dl",
    "pm",
)
CITATION_SOURCE_FIELDS: Mapping[str, str] = {
    "AU": "au",
    "TI": "ti",
    "SO": "so",
    "VL": "vl",
    "BP": "bp",
    "EP": "ep",
    "AR": "ar",
    "PY": "py",
    "DI": "di",
    "PM": "pm",
}
CITATION_STATUS_KEY = "_citation_metadata_status"
CITATION_STATUS_MATCHED = "matched"
CITATION_STATUS_MISSING = "missing"
CITATION_STATUS_LOOKUP_FAILED = "lookup_failed"

_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_HOSTS = frozenset(("doi.org", "dx.doi.org"))
_DOI_SCHEMES = frozenset(("http", "https"))


def normalize_doi(
    value: object,
    *,
    require_resolver_url: bool = False,
) -> str | None:
    """Return one valid DOI while rejecting arbitrary URLs and whitespace."""
    if not isinstance(value, str) or not value.strip():
        return None

    raw = value.strip()
    if raw.startswith("doi:"):
        if require_resolver_url:
            return None
        return _normalize_doi_body(raw.removeprefix("doi:").lstrip())

    parsed = urlsplit(raw)
    if parsed.scheme:
        return _normalize_resolver_url(parsed)
    if require_resolver_url:
        return None
    return _normalize_doi_body(raw)


def canonical_doi_urls(doi: str) -> tuple[str, str]:
    """Return the visible and safely encoded canonical DOI resolver URLs."""
    visible = f"https://doi.org/{doi}"
    target = f"https://doi.org/{quote(doi, safe='/-._~')}"
    return visible, target


def _normalize_resolver_url(parsed) -> str | None:
    """Extract a DOI only from an exact approved resolver URL."""
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in _DOI_SCHEMES:
        return None
    if hostname is None or hostname.lower() not in _DOI_HOSTS:
        return None
    if parsed.netloc.lower() != hostname.lower() or port is not None:
        return None
    if parsed.query or parsed.fragment or not parsed.path.startswith("/"):
        return None
    return _normalize_doi_body(parsed.path.removeprefix("/"))


def _normalize_doi_body(value: str) -> str | None:
    """Decode and validate one DOI body while preserving its source case."""
    doi = unquote(value)
    return doi if _DOI_PATTERN.fullmatch(doi) else None


__all__ = [
    "CITATION_RECORD_FIELDS",
    "CITATION_SOURCE_FIELDS",
    "CITATION_STATUS_KEY",
    "CITATION_STATUS_LOOKUP_FAILED",
    "CITATION_STATUS_MATCHED",
    "CITATION_STATUS_MISSING",
    "canonical_doi_urls",
    "normalize_doi",
]
