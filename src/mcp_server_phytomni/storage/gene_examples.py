# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure, bounded contract for explicitly approved curated gene bundles.

This module has no application imports, configuration loads or network I/O.
Ordinary agent outputs are never discovered or published by this contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 6 * 1024 * 1024
MAX_EXCERPT_BYTES = 64 * 1024
MAX_EXCERPTS_BYTES = 4 * 1024 * 1024
MAX_REFERENCES = 999
MAX_RESOURCES = 999
MAX_JSON_DEPTH = 8
GENE_PATTERN = r"(?:AT|GLYMA|Os|Traes|Zm)[A-Za-z0-9.-]*"
RESOURCE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
SHA256_PATTERN = r"[0-9a-f]{64}"
ResourceKind = Literal["image", "cif", "markdown"]
RESOURCE_TYPES = {
    "image": (".png", "image/png"),
    "cif": (".cif", "chemical/x-cif"),
    "markdown": (".md", "text/markdown"),
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_HTML_ENTRY = re.compile(
    r"^(?:<!--|<!doctype html(?:[\s>])|"
    r"<(?:html|head|body|script|iframe|h1|div|font|table|a|style|title|b|br|p)"
    r"(?:[\s>]))",
    re.IGNORECASE,
)
_HTML_DOCUMENT = re.compile(
    r"^(?:<!doctype[\t\n\r ]+html\b|<(?:html|head|body)(?:[\t\n\r >]))",
    re.IGNORECASE,
)


class CuratedGeneError(ValueError):
    """Expose a stable error code and HTTP status, never private paths."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def _require(condition: bool, code: str = "invalid_manifest") -> None:
    """Reject a failed contract invariant without leaking its input."""
    if not condition:
        raise CuratedGeneError(code, 409)


def _has_controls(value: str) -> bool:
    """Identify forbidden metadata control characters."""
    return any(unicodedata.category(char) == "Cc" for char in value)


def _validate_gene(gene: str) -> None:
    """Preserve the established case-sensitive curated gene grammar."""
    _require(bool(re.fullmatch(GENE_PATTERN, gene)))


def manifest_key(gene: str) -> str:
    """Derive the only manifest object key for one validated gene."""
    _validate_gene(gene)
    return f"gene-examples/manifests/{gene}_result.json"


def parse_manifest_key(key: str) -> str | None:
    """Recognize an exact manifest key without unescaping user input."""
    match = re.fullmatch(
        rf"gene-examples/manifests/({GENE_PATTERN})_result\.json", key
    )
    return match.group(1) if match else None


def parse_material_key(key: str) -> tuple[str, str, str, str] | None:
    """Recognize a report-bound Markdown or CIF material object key."""
    match = re.fullmatch(
        rf"gene-examples/materials/({GENE_PATTERN})/"
        rf"({SHA256_PATTERN})/({RESOURCE_ID_PATTERN})\.(md|cif)",
        key,
    )
    if match is None:
        return None
    gene, revision, resource_id, extension = match.groups()
    kind = "markdown" if extension == "md" else "cif"
    return gene, revision, resource_id, kind


def resource_object_key(
    gene: str, report_sha: str, resource_id: str, kind: str
) -> str:
    """Derive a closed catalog locator; no arbitrary paths are accepted."""
    _validate_gene(gene)
    _require(bool(re.fullmatch(SHA256_PATTERN, report_sha)))
    _require(bool(re.fullmatch(RESOURCE_ID_PATTERN, resource_id)))
    _require(kind in RESOURCE_TYPES)
    extension = RESOURCE_TYPES[kind][0]
    if kind == "image":
        return f"gene-examples/img/{gene}/{gene}_{resource_id}.png"
    return (
        f"gene-examples/materials/{gene}/{report_sha}/"
        f"{resource_id}{extension}"
    )


class _StrictModel(BaseModel):
    """Share exact fields and non-coercing types across input and output."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ResourceAssociation(_StrictModel):
    """Declare presentation identity independently from storage locators."""

    id: str = Field(pattern=rf"^{RESOURCE_ID_PATTERN}$")
    name: str
    kind: ResourceKind
    markdown_href: str

    @model_validator(mode="after")
    def _association(self) -> Self:
        """Validate safe display names and literal authored-link mappings."""
        _require(1 <= len(self.name.encode("utf-8")) <= 255)
        _require(not _has_controls(self.name))
        _require(not any(char in self.name for char in "/\\"))
        _require(self.name.endswith(RESOURCE_TYPES[self.kind][0]))
        href = self.markdown_href
        _require(1 <= len(href.encode("utf-8")) <= 2048)
        _require(not _has_controls(href))
        _require(not any(char in href for char in "\\?#%<>\"'"))
        _require(not href.startswith("//"))
        _require(not href.startswith("/") or href.startswith("/api/"))
        _require(not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", href))
        _require(href == href.strip())
        return self


class CuratedGeneResource(_ResourceAssociation):
    """One declared resource, bound to exact bytes and an authored link."""

    object_key: str
    media_type: str
    size_bytes: int = Field(ge=1, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=rf"^{SHA256_PATTERN}$")

    @model_validator(mode="after")
    def _media_type(self) -> Self:
        """Require the single supported MIME type for each resource kind."""
        _require(self.media_type == RESOURCE_TYPES[self.kind][1])
        return self


class ReferenceMaterial(_StrictModel):
    """An explicit one-based slot; empty excerpts remain meaningful."""

    reference_index: int = Field(ge=1, le=MAX_REFERENCES)
    excerpt: str
    resource_ids: list[str] = Field(max_length=MAX_RESOURCES)

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        """Keep excerpts byte-bounded and resource references unambiguous."""
        _require(len(self.excerpt.encode("utf-8")) <= MAX_EXCERPT_BYTES)
        _require(len(set(self.resource_ids)) == len(self.resource_ids))
        return self


class _GeneDocument[ResourceT: _ResourceAssociation](_StrictModel):
    """Share report identity and explicit ordered source slots."""

    schema_version: int
    gene_id: str
    report_file: str
    reference_count: int = Field(ge=0, le=MAX_REFERENCES)
    resources: list[ResourceT] = Field(max_length=MAX_RESOURCES)
    reference_materials: list[ReferenceMaterial] = Field(
        max_length=MAX_REFERENCES
    )

    @model_validator(mode="after")
    def _identity_and_slots(self) -> Self:
        """Validate exact identity, duplicate declarations and slot order."""
        _require(self.schema_version == 1)
        _validate_gene(self.gene_id)
        _require(self.report_file == f"{self.gene_id}_result.md")
        _require(len(self.reference_materials) == self.reference_count)
        ids = [resource.id for resource in self.resources]
        hrefs = [resource.markdown_href for resource in self.resources]
        _require(len(set(ids)) == len(ids))
        _require(len(set(hrefs)) == len(hrefs))
        total = 0
        for index, material in enumerate(self.reference_materials, 1):
            _require(material.reference_index == index)
            _require(set(material.resource_ids).issubset(ids))
            total += len(material.excerpt.encode("utf-8"))
        _require(total <= MAX_EXCERPTS_BYTES)
        return self


class CuratedGeneManifest(_GeneDocument[CuratedGeneResource]):
    """Strict version-1 wire manifest; internal locators are server-only."""

    report_sha256: str = Field(pattern=rf"^{SHA256_PATTERN}$")

    @model_validator(mode="after")
    def _object_bindings(self) -> Self:
        """Bind every locator to this gene, report digest and resource ID."""
        for resource in self.resources:
            _require(
                resource.object_key
                == resource_object_key(
                    self.gene_id,
                    self.report_sha256,
                    resource.id,
                    resource.kind,
                )
            )
        return self


class _ApprovedResource(_ResourceAssociation):
    """Accept an explicit source file, never caller-computed provenance."""

    source_file: str


class _ApprovedInput(_GeneDocument[_ApprovedResource]):
    """Represent the operator-approved local import declaration."""

    report_source: str


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON fields at every object depth."""
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    """Reject non-standard NaN and Infinity JSON literals."""
    raise CuratedGeneError("invalid_manifest", 409)


def _decode_json(raw: bytes) -> object:
    """Apply byte and nesting budgets before standard JSON decoding."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CuratedGeneError("content_too_large", 413)
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (123, 91):
            depth += 1
            _require(depth <= MAX_JSON_DEPTH)
        elif byte in (125, 93):
            depth -= 1
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_pairs,
        parse_constant=_reject_constant,
    )


def decode_gene_manifest(raw: bytes) -> CuratedGeneManifest:
    """Reject malformed, ambiguous or over-budget manifests safely."""
    try:
        return CuratedGeneManifest.model_validate(_decode_json(raw))
    except CuratedGeneError:
        raise
    except UnicodeDecodeError:
        raise CuratedGeneError("invalid_content", 422) from None
    except (ValueError, TypeError, RecursionError):
        raise CuratedGeneError("invalid_manifest", 409) from None


def _validate_content(raw: bytes, kind: str) -> None:
    """Check bounded bytes and format entry points, not scientific validity."""
    if len(raw) > MAX_FILE_BYTES:
        raise CuratedGeneError("content_too_large", 413)
    if not raw:
        raise CuratedGeneError("invalid_content", 422)
    if kind == "image":
        if not raw.startswith(_PNG_SIGNATURE):
            raise CuratedGeneError("invalid_content", 422)
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CuratedGeneError("invalid_content", 422) from None
    leading = text.strip().removeprefix("\ufeff").lstrip()
    html_pattern = _HTML_DOCUMENT if kind == "report" else _HTML_ENTRY
    if not leading or "\x00" in text or html_pattern.match(leading):
        raise CuratedGeneError("invalid_content", 422)
    if kind == "cif":
        first = next(
            (
                line.strip()
                for line in leading.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ),
            "",
        )
        if not first.startswith("data_") or len(first) <= 5:
            raise CuratedGeneError("invalid_content", 422)


def validate_report_bytes(
    raw: bytes, expected_sha256: str | None = None
) -> None:
    """Validate text without rewriting original report bytes."""
    _validate_content(raw, "report")
    if expected_sha256 is not None:
        _require(
            hashlib.sha256(raw).hexdigest() == expected_sha256,
            "report_binding_changed",
        )


def validate_material_bytes(raw: bytes, resource: CuratedGeneResource) -> None:
    """Verify a declared material's budget, exact identity and content."""
    if len(raw) > MAX_FILE_BYTES:
        raise CuratedGeneError("content_too_large", 413)
    _require(len(raw) == resource.size_bytes, "material_binding_changed")
    _require(
        hashlib.sha256(raw).hexdigest() == resource.sha256,
        "material_binding_changed",
    )
    _validate_content(raw, resource.kind)


def _relative_parts(value: str) -> list[str]:
    """Require a normalized relative POSIX file path with no traversal."""
    parts = value.split("/")
    _require(not _has_controls(value), "invalid_source")
    _require(not any(char in value for char in "\\:"), "invalid_source")
    _require(
        all(part and part not in (".", "..") for part in parts),
        "invalid_source",
    )
    return parts


def _open_directory(path: Path) -> int:
    """Open every ancestor without following any symlink aliases."""
    path = path.absolute()
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            _require(part not in (".", ".."), "invalid_source")
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_declared(root: Path, relative: str, limit: int) -> bytes:
    """Read a bounded regular file through symlink-resistant directory FDs."""
    parts = _relative_parts(relative)
    descriptor = _open_directory(root)
    try:
        for part in parts[:-1]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        with os.fdopen(file_descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            _require(stat.S_ISREG(info.st_mode), "invalid_source")
            if info.st_size > limit:
                raise CuratedGeneError("content_too_large", 413)
            raw = source.read(limit + 1)
            if len(raw) > limit:
                raise CuratedGeneError("content_too_large", 413)
            return raw
    finally:
        os.close(descriptor)


def _write_bundle_file(root: Path, key: str, raw: bytes) -> None:
    """Create one file inside this compiler's private temporary directory."""
    path = root.joinpath(*_relative_parts(key))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as destination:
        destination.write(raw)


def _compile_resource(
    source: Path,
    staging: Path,
    gene: str,
    revision: str,
    item: _ApprovedResource,
) -> CuratedGeneResource:
    """Validate and stage one approved resource with byte-derived metadata."""
    raw = _read_declared(source, item.source_file, MAX_FILE_BYTES)
    _validate_content(raw, item.kind)
    resource = CuratedGeneResource(
        **item.model_dump(exclude={"source_file"}),
        object_key=resource_object_key(gene, revision, item.id, item.kind),
        media_type=RESOURCE_TYPES[item.kind][1],
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    validate_material_bytes(raw, resource)
    _write_bundle_file(staging, resource.object_key, raw)
    return resource


def build_gene_bundle(
    approved_json: bytes, source_root: Path, output_root: Path
) -> CuratedGeneManifest:
    """Compile only approved files atomically into a local catalog bundle."""
    try:
        approved = _ApprovedInput.model_validate(_decode_json(approved_json))
        source = source_root.absolute()
        output = output_root.absolute()
        _require(not output.is_relative_to(source), "overlapping_roots")
        _require(not source.is_relative_to(output), "overlapping_roots")
        for root in (source, output.parent):
            os.close(_open_directory(root))
        _require(not output.is_symlink(), "invalid_output")
        _require(
            not output.exists()
            or (output.is_dir() and not any(output.iterdir())),
            "output_exists",
        )
        report = _read_declared(source, approved.report_source, MAX_FILE_BYTES)
        validate_report_bytes(report)
        revision = hashlib.sha256(report).hexdigest()
        with tempfile.TemporaryDirectory(
            prefix=".curated-gene-", dir=output.parent
        ) as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            _write_bundle_file(
                staging, f"gene-examples/md/{approved.report_file}", report
            )
            resources = [
                _compile_resource(
                    source, staging, approved.gene_id, revision, item
                )
                for item in approved.resources
            ]
            manifest = CuratedGeneManifest(
                **approved.model_dump(exclude={"report_source", "resources"}),
                report_sha256=revision,
                resources=resources,
            )
            raw_manifest = (
                json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)
                + "\n"
            ).encode("utf-8")
            decode_gene_manifest(raw_manifest)
            _write_bundle_file(
                staging, manifest_key(approved.gene_id), raw_manifest
            )
            staging.rename(output)
            return manifest
    except CuratedGeneError:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        raise CuratedGeneError("invalid_bundle", 409) from None


def check_gene_bundle(root: Path, gene: str) -> CuratedGeneManifest:
    """Read and validate an existing bundle without modifying any bytes."""
    try:
        manifest = decode_gene_manifest(
            _read_declared(root, manifest_key(gene), MAX_MANIFEST_BYTES)
        )
        _require(manifest.gene_id == gene)
        validate_report_bytes(
            _read_declared(
                root,
                f"gene-examples/md/{manifest.report_file}",
                MAX_FILE_BYTES,
            ),
            manifest.report_sha256,
        )
        for resource in manifest.resources:
            validate_material_bytes(
                _read_declared(root, resource.object_key, MAX_FILE_BYTES),
                resource,
            )
        return manifest
    except CuratedGeneError:
        raise
    except (OSError, ValueError, TypeError):
        raise CuratedGeneError("invalid_bundle", 409) from None
