# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Tests for pure citation metadata and DOI contracts."""

import pytest

from mcp_server_phytomni.agents.shared.citation_metadata import (
    CITATION_RECORD_FIELDS,
    CITATION_SOURCE_FIELDS,
    CITATION_STATUS_KEY,
    CITATION_STATUS_LOOKUP_FAILED,
    CITATION_STATUS_MATCHED,
    CITATION_STATUS_MISSING,
    canonical_doi_urls,
    normalize_doi,
)

pytestmark = pytest.mark.unit


def test_citation_metadata_field_mapping_and_status_contract() -> None:
    """The source mapping excludes the nullable compatibility ``dl`` field."""
    assert CITATION_RECORD_FIELDS == (
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
    assert CITATION_SOURCE_FIELDS == {
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
    assert "dl" in CITATION_RECORD_FIELDS
    assert "DL" not in CITATION_SOURCE_FIELDS
    assert CITATION_STATUS_KEY == "_citation_metadata_status"
    assert {
        CITATION_STATUS_MATCHED,
        CITATION_STATUS_MISSING,
        CITATION_STATUS_LOOKUP_FAILED,
    } == {"matched", "missing", "lookup_failed"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1007/978-1-4939-2639-8_6", "10.1007/978-1-4939-2639-8_6"),
        (" doi: 10.1038/S41586-026-10798-9 ", "10.1038/S41586-026-10798-9"),
        ("https://doi.org/10.1000/a%28b%29", "10.1000/a(b)"),
        ("http://dx.doi.org/10.1000/x", "10.1000/x"),
    ],
)
def test_normalize_doi_accepts_approved_forms(
    raw: str,
    expected: str,
) -> None:
    """Bare, prefixed, and approved DOI resolver forms normalize."""
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "doi:",
        "11.1000/x",
        "10.1000/has space",
        "ftp://doi.org/10.1/x",
        "https://example.com/10.1000/x",
        "https://doi.org/10.1000/x?q=1",
        "https://doi.org/10.1000/x#fragment",
        "https://user@doi.org/10.1000/x",
        "https://doi.org/10.1000/a%20b",
        "10.1000/x](https://evil.test)",
        "10.1000/x[evil",
        "10.1000/x\\evil",
        "10.1000/x\x7fhidden",
    ],
)
def test_normalize_doi_rejects_non_contract_values(raw: object) -> None:
    """Only contract-approved DOI values can become citation links."""
    assert normalize_doi(raw) is None


def test_dl_mode_requires_a_doi_resolver_url() -> None:
    """The compatibility ``dl`` field cannot supply an arbitrary DOI body."""
    assert normalize_doi("10.1000/x", require_resolver_url=True) is None
    assert (
        normalize_doi("https://doi.org/10.1000/x", require_resolver_url=True)
        == "10.1000/x"
    )


def test_canonical_doi_target_encodes_markdown_unsafe_path_characters() -> (
    None
):
    """Labels retain the DOI while Markdown targets use encoded paths."""
    assert canonical_doi_urls("10.1000/a(b)?c") == (
        "https://doi.org/10.1000/a(b)?c",
        "https://doi.org/10.1000/a%28b%29%3Fc",
    )
