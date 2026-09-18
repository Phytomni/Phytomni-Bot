# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Strict, offline curated gene material contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from tests.support.curated_gene import protocol_association

from mcp_server_phytomni.storage.gene_examples import (
    CuratedGeneError,
    build_gene_bundle,
    check_gene_bundle,
    decode_gene_manifest,
    manifest_key,
    parse_manifest_key,
    parse_material_key,
    validate_material_bytes,
    validate_report_bytes,
)

GENE = "Os01g0177400"
REPORT = b"# Approved report\r\n\n[Protocol](./protocol.md) [1]\n"
PROTOCOL = "# Protocol\r\n\nApproved α procedure.\n".encode()
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


def digest(raw: bytes) -> str:
    """Hash exact source bytes without newline normalization."""
    return hashlib.sha256(raw).hexdigest()


def manifest_data() -> dict:
    """Return a fully declared approved fixture with explicit empty slots."""
    report_hash = digest(REPORT)
    return {
        "schema_version": 1,
        "gene_id": GENE,
        "report_file": f"{GENE}_result.md",
        "report_sha256": report_hash,
        "reference_count": 2,
        "resources": [
            {
                **protocol_association(),
                "object_key": (
                    f"gene-examples/materials/{GENE}/{report_hash}/protocol.md"
                ),
                "size_bytes": len(PROTOCOL),
                "sha256": digest(PROTOCOL),
            }
        ],
        "reference_materials": [
            {"reference_index": 1, "excerpt": "same", "resource_ids": []},
            {
                "reference_index": 2,
                "excerpt": "",
                "resource_ids": ["protocol"],
            },
        ],
    }


def encoded(data: dict) -> bytes:
    """Encode the public schema fixture as UTF-8 JSON."""
    return json.dumps(data, ensure_ascii=False).encode()


def test_manifest_preserves_order_and_explicit_empty_slots():
    """Retain every declared slot and derive only approved catalog keys."""
    data = manifest_data()
    manifest = decode_gene_manifest(encoded(data))
    assert manifest.model_dump() == data
    assert [item.reference_index for item in manifest.reference_materials] == [
        1,
        2,
    ]
    assert manifest.reference_materials[1].excerpt == ""
    assert manifest_key(GENE) == f"gene-examples/manifests/{GENE}_result.json"
    assert parse_manifest_key(manifest_key(GENE)) == GENE
    key = manifest.resources[0].object_key
    assert parse_material_key(key) == (
        GENE,
        digest(REPORT),
        "protocol",
        "markdown",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("gene_id", "../Os01"),
        ("gene_id", "os01"),
        ("gene_id", "Os01_fake"),
        ("report_file", "another_result.md"),
        ("report_sha256", "A" * 64),
        ("reference_count", 1000),
        ("reference_count", True),
        ("reference_count", -1),
        ("resources", None),
        ("unknown", "private"),
    ],
)
def test_manifest_rejects_wrong_top_level_fields(field, value):
    """Reject malformed report identity and unknown top-level metadata."""
    data = manifest_data()
    data[field] = value
    with pytest.raises(CuratedGeneError) as caught:
        decode_gene_manifest(encoded(data))
    assert caught.value.status == 409
    assert "private" not in str(caught.value)


def test_gene_grammar_does_not_widen_existing_catalog_to_underscore():
    """Keep gene identifiers within the original curated relay grammar."""
    raw = encoded(manifest_data()).replace(GENE.encode(), b"Os01_fake")
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(raw)
    assert (
        parse_manifest_key("gene-examples/manifests/Os01_fake_result.json")
        is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("markdown_href", "/private/protocol.md"),
        ("markdown_href", "./proto\u0080col.md"),
        ("name", "proto\u0080col.md"),
    ],
)
def test_metadata_boundary_matches_go_reader(field, value):
    """Reject private absolute links and Unicode controls across both lanes."""
    data = manifest_data()
    data["resources"][0][field] = value
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(encoded(data))


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ".."),
        ("id", "x/y"),
        ("kind", "html"),
        ("name", "../protocol.md"),
        ("name", "protocol.html"),
        ("markdown_href", "https://private.invalid/source"),
        ("markdown_href", "//private/source"),
        ("markdown_href", "./a%2eb.md"),
        ("markdown_href", "./a.md?x=1"),
        ("markdown_href", "./a.md#fragment"),
        ("markdown_href", './a".md'),
        ("markdown_href", "./a\n.md"),
        ("object_key", "gene-examples/materials/AT1/" + "0" * 64 + "/x.md"),
        ("media_type", "text/html"),
        ("size_bytes", True),
        ("size_bytes", 0),
        ("size_bytes", 8 * 1024 * 1024 + 1),
        ("sha256", "bad"),
        ("source_file", "private"),
    ],
)
def test_manifest_rejects_invalid_resource(field, value):
    """Reject malformed resource identity, type, locator and display fields."""
    data = manifest_data()
    data["resources"][0][field] = value
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(encoded(data))


def test_manifest_rejects_duplicate_json_keys_at_any_level():
    """Reject duplicate root and nested JSON fields before normalization."""
    raw = encoded(manifest_data())
    for invalid in (
        raw.replace(
            b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1'
        ),
        raw.replace(
            b'"id": "protocol"', b'"id": "protocol", "id": "protocol"'
        ),
    ):
        with pytest.raises(CuratedGeneError):
            decode_gene_manifest(invalid)


@pytest.mark.parametrize(
    "raw,status",
    [
        (b" " * (6 * 1024 * 1024 + 1), 413),
        (b"[" * 1000 + b"]" * 1000, 409),
        (b'{"schema_version": NaN}', 409),
        (b"\xff", 422),
    ],
)
def test_manifest_bounds_before_decode(raw, status):
    """Classify oversized, over-nested and malformed inputs safely."""
    with pytest.raises(CuratedGeneError) as caught:
        decode_gene_manifest(raw)
    assert caught.value.status == status


def test_invalid_manifest_utf8_is_content_error_but_bad_unicode_is_schema():
    """Distinguish invalid UTF-8 bytes from unpaired JSON Unicode escapes."""
    with pytest.raises(CuratedGeneError) as caught:
        decode_gene_manifest(b"\xff\xfe")
    assert caught.value.status == 422
    assert caught.value.code == "invalid_content"
    raw = encoded(manifest_data()).replace(b'"same"', b'"\\ud800"', 1)
    with pytest.raises(CuratedGeneError) as caught:
        decode_gene_manifest(raw)
    assert caught.value.status == 409
    assert caught.value.code == "invalid_manifest"


@pytest.mark.parametrize(
    "mode",
    [
        "sparse",
        "reordered",
        "duplicate",
        "missing",
        "unknown_resource",
        "duplicate_resource",
        "oversized_excerpt",
        "null_excerpt",
    ],
)
def test_manifest_rejects_invalid_slots(mode):
    """Reject ambiguous ordering, missing slots and invalid associations."""
    data = manifest_data()
    slots = data["reference_materials"]
    if mode == "sparse":
        slots[0]["reference_index"] = 999999999
    elif mode == "reordered":
        slots.reverse()
    elif mode == "duplicate":
        slots[1]["reference_index"] = 1
    elif mode == "missing":
        slots.pop()
    elif mode == "unknown_resource":
        slots[0]["resource_ids"] = ["unknown"]
    elif mode == "duplicate_resource":
        slots[0]["resource_ids"] = ["protocol", "protocol"]
    elif mode == "oversized_excerpt":
        slots[0]["excerpt"] = "α" * 32769
    else:
        slots[0]["excerpt"] = None
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(encoded(data))


def test_manifest_rejects_total_excerpt_overflow_and_duplicate_resources():
    """Apply the combined excerpt budget and unique resource identity rule."""
    data = manifest_data()
    data["reference_count"] = 65
    data["reference_materials"] = [
        {"reference_index": i + 1, "excerpt": "x" * 65536, "resource_ids": []}
        for i in range(65)
    ]
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(encoded(data))
    data = manifest_data()
    data["resources"] *= 2
    with pytest.raises(CuratedGeneError):
        decode_gene_manifest(encoded(data))


def test_material_bytes_require_exact_size_hash_and_type():
    """Validate declared bytes without accepting oversized or invalid text."""
    resource = decode_gene_manifest(encoded(manifest_data())).resources[0]
    validate_material_bytes(PROTOCOL, resource)
    validate_report_bytes(REPORT, digest(REPORT))
    for raw, status in [(b"x", 409), (b"x" * (8 * 1024 * 1024 + 1), 413)]:
        with pytest.raises(CuratedGeneError) as caught:
            validate_material_bytes(raw, resource)
        assert caught.value.status == status
    for raw in (
        b"\xff",
        b"<!DOCTYPE html><html>Error</html>",
        b"\xef\xbb\xbf <HTML>Error</HTML>",
        b"# Report\n\x00binary",
    ):
        with pytest.raises(CuratedGeneError) as caught:
            validate_report_bytes(raw)
        assert caught.value.status == 422


@pytest.mark.parametrize("raw", [b"<html>Changed</html>", b"\xff", b""])
def test_changed_registered_bytes_report_conflict_before_content(raw):
    """Classify changed registered bytes as conflicts before format checks."""
    resource = decode_gene_manifest(encoded(manifest_data())).resources[0]
    with pytest.raises(CuratedGeneError) as caught:
        validate_material_bytes(raw, resource)
    assert caught.value.status == 409
    assert caught.value.code == "material_binding_changed"


def test_changed_png_conflicts_but_declared_invalid_png_is_malformed():
    """Differentiate mutated PNG content from a matching malformed source."""
    data = manifest_data()
    data["resources"][0].update(
        kind="image",
        name="figure.png",
        media_type="image/png",
        object_key=f"gene-examples/img/{GENE}/{GENE}_protocol.png",
        size_bytes=len(PNG),
        sha256=digest(PNG),
    )
    resource = decode_gene_manifest(encoded(data)).resources[0]
    malformed = b"not a PNG"
    with pytest.raises(CuratedGeneError) as caught:
        validate_material_bytes(malformed, resource)
    assert caught.value.status == 409
    data["resources"][0].update(
        size_bytes=len(malformed), sha256=digest(malformed)
    )
    resource = decode_gene_manifest(encoded(data)).resources[0]
    with pytest.raises(CuratedGeneError) as caught:
        validate_material_bytes(malformed, resource)
    assert caught.value.status == 422


def test_report_preserves_legal_markdown_html_blocks():
    """Preserve authored Markdown HTML blocks without changing the digest."""
    raw = b"<div>\nDOC TITLES\n\n1. Not a source\n</div>\n"
    validate_report_bytes(raw, digest(raw))


@pytest.mark.parametrize(
    "raw",
    [b" \r\n\t", b"\xef\xbb\xbf \n", b"<!DOCTYPE\thtml><html>Error</html>"],
)
def test_report_rejects_empty_text_and_html_document_whitespace(raw):
    """Reject blank reports and HTML documents with alternate whitespace."""
    with pytest.raises(CuratedGeneError) as caught:
        validate_report_bytes(raw)
    assert caught.value.status == 422


@pytest.mark.parametrize(
    "raw",
    [
        b"<script>Error</script>",
        b"<div>Server error</div>",
        b"<!-- error --><html>Bad</html>",
        b"# Text\n\x00binary",
    ],
)
def test_material_rejects_html_entry_and_nul_with_matching_digest(raw):
    """Reject malformed materials even when the declared hash matches."""
    data = manifest_data()
    data["resources"][0].update(size_bytes=len(raw), sha256=digest(raw))
    resource = decode_gene_manifest(encoded(data)).resources[0]
    with pytest.raises(CuratedGeneError) as caught:
        validate_material_bytes(raw, resource)
    assert caught.value.status == 422


def approved_input(source: Path) -> dict:
    """Write only synthetic test files and explicit input references."""
    source.mkdir()
    (source / "report.md").write_bytes(REPORT)
    (source / "protocol.md").write_bytes(PROTOCOL)
    data = manifest_data()
    data.pop("report_sha256")
    data["report_source"] = "report.md"
    resource = data["resources"][0]
    for key in ("object_key", "media_type", "size_bytes", "sha256"):
        resource.pop(key)
    resource["source_file"] = "protocol.md"
    return data


def test_offline_bundle_preserves_bytes_and_check_is_read_only(tmp_path):
    """Preserve source bytes and validate without changing bundle metadata."""
    source = tmp_path / "source"
    data = approved_input(source)
    output = tmp_path / "output"
    manifest = build_gene_bundle(encoded(data), source, output)
    assert (
        output / f"gene-examples/md/{GENE}_result.md"
    ).read_bytes() == REPORT
    assert (output / manifest.resources[0].object_key).read_bytes() == PROTOCOL
    before = (output / manifest_key(GENE)).stat().st_mtime_ns
    assert check_gene_bundle(output, GENE) == manifest
    assert (output / manifest_key(GENE)).stat().st_mtime_ns == before
    with pytest.raises(CuratedGeneError):
        build_gene_bundle(encoded(data), source, output)


@pytest.mark.parametrize(
    "mode",
    [
        "parent",
        "symlink_file",
        "symlink_directory",
        "missing",
        "unknown_field",
        "root_overlap",
    ],
)
def test_failed_bundle_never_publishes_output(tmp_path, mode):
    """Leave no final bundle when a source or input declaration is unsafe."""
    source = tmp_path / "source"
    data = approved_input(source)
    output = tmp_path / "output"
    if mode == "parent":
        data["resources"][0]["source_file"] = "../private.md"
    elif mode == "symlink_file":
        (source / "alias.md").symlink_to(source / "protocol.md")
        data["resources"][0]["source_file"] = "alias.md"
    elif mode == "symlink_directory":
        (source / "alias").symlink_to(source, target_is_directory=True)
        data["resources"][0]["source_file"] = "alias/protocol.md"
    elif mode == "missing":
        data["resources"][0]["source_file"] = "missing.md"
    elif mode == "unknown_field":
        data["resources"][0]["sha256"] = "0" * 64
    else:
        output = source / "output"
    with pytest.raises(CuratedGeneError):
        build_gene_bundle(encoded(data), source, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "kind,name,raw",
    [
        ("cif", "structure.cif", b"data_approved\r\n# entry\n"),
        ("cif", "structure.cif", b"\xef\xbb\xbf# comment\ndata_approved\n"),
        ("image", "figure.png", PNG),
    ],
)
def test_bundle_all_material_kinds_are_byte_exact(tmp_path, kind, name, raw):
    """Keep PNG and CIF bytes, including a supported UTF-8 BOM, unchanged."""
    source = tmp_path / "source"
    data = approved_input(source)
    (source / name).write_bytes(raw)
    item = data["resources"][0]
    item.update(
        id="approved",
        name=name,
        kind=kind,
        markdown_href=f"../../approved/{name}",
        source_file=name,
    )
    data["reference_materials"][1]["resource_ids"] = ["approved"]
    output = tmp_path / "output"
    manifest = build_gene_bundle(encoded(data), source, output)
    assert (output / manifest.resources[0].object_key).read_bytes() == raw
    assert check_gene_bundle(output, GENE) == manifest


@pytest.mark.parametrize(
    "raw", [b"not a CIF", b"<html>Error</html>", b"data_"]
)
def test_invalid_cif_does_not_publish_bundle(tmp_path, raw):
    """Reject invalid CIF format entries without publishing partial output."""
    source = tmp_path / "source"
    data = approved_input(source)
    (source / "bad.cif").write_bytes(raw)
    data["resources"][0].update(
        kind="cif", name="bad.cif", source_file="bad.cif"
    )
    with pytest.raises(CuratedGeneError):
        build_gene_bundle(encoded(data), source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_empty_output_allowed_but_source_root_symlinks_rejected(tmp_path):
    """Allow an empty output directory but never follow source-root aliases."""
    source = tmp_path / "source"
    data = approved_input(source)
    alias = tmp_path / "source-alias"
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(CuratedGeneError):
        build_gene_bundle(encoded(data), alias, tmp_path / "rejected")
    output = tmp_path / "output"
    output.mkdir()
    build_gene_bundle(encoded(data), source, output)
    assert check_gene_bundle(output, GENE).reference_count == 2


def test_exact_limits_and_repeated_excerpts_preserve_all_slots():
    """Accept exact excerpt budgets without deduplicating repeated science."""
    data = manifest_data()
    data["reference_count"] = 64
    data["reference_materials"] = [
        {"reference_index": i + 1, "excerpt": "α" * 32768, "resource_ids": []}
        for i in range(64)
    ]
    manifest = decode_gene_manifest(encoded(data))
    assert len(manifest.reference_materials) == 64
    assert (
        manifest.reference_materials[0].excerpt
        == manifest.reference_materials[-1].excerpt
    )


def test_nonempty_existing_output_is_never_modified(tmp_path):
    """Keep pre-existing user output intact when compilation is rejected."""
    source = tmp_path / "source"
    data = approved_input(source)
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "user-owned"
    marker.write_bytes(b"preserve")
    with pytest.raises(CuratedGeneError):
        build_gene_bundle(encoded(data), source, output)
    assert list(output.iterdir()) == [marker]
    assert marker.read_bytes() == b"preserve"


def test_check_rejects_symlink_and_report_revision_change(tmp_path):
    """Reject material aliases and stale reports during bundle checks."""
    source = tmp_path / "source"
    data = approved_input(source)
    output = tmp_path / "output"
    manifest = build_gene_bundle(encoded(data), source, output)
    resource = output / manifest.resources[0].object_key
    resource.unlink()
    resource.symlink_to(source / "protocol.md")
    with pytest.raises(CuratedGeneError):
        check_gene_bundle(output, GENE)
    resource.unlink()
    resource.write_bytes(PROTOCOL)
    (output / f"gene-examples/md/{GENE}_result.md").write_bytes(b"new report")
    with pytest.raises(CuratedGeneError) as caught:
        check_gene_bundle(output, GENE)
    assert caught.value.code == "report_binding_changed"


def test_check_detects_changed_report_and_material(tmp_path):
    """Detect changed material bytes when rechecking an existing bundle."""
    source = tmp_path / "source"
    data = approved_input(source)
    output = tmp_path / "output"
    manifest = build_gene_bundle(encoded(data), source, output)
    path = output / manifest.resources[0].object_key
    path.write_bytes(b"changed")
    with pytest.raises(CuratedGeneError):
        check_gene_bundle(output, GENE)
