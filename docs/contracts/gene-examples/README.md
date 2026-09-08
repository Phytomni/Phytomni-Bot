# Gene Examples OBS Contract

This contract defines the Bot-side read boundary for the curated gene-example
catalog. It is a shape and authorization contract for Bot, Web, operators,
and approved future producers; it is not production acceptance evidence.

## Access

Bot exposes this catalog only through authenticated `relay:obs` reads. The
public Web gene page does not make the Bot relay anonymous.

The relay must be enabled with `RELAY_ENABLED=1`, and the caller key must
carry the `relay:obs` scope. Response-size caps, streaming cleanup, and
metadata-only audit remain active for these reads.

## Objects

```text
gene-examples/md/<GENE>_result.md
gene-examples/img/<GENE>/<GENE>_<name>.png
gene-examples/manifests/<GENE>_result.json
gene-examples/materials/<GENE>/<REPORT_SHA256>/<RESOURCE_ID>.md
gene-examples/materials/<GENE>/<REPORT_SHA256>/<RESOURCE_ID>.cif
```

`GENE` starts with `AT`, `GLYMA`, `Os`, `Traes`, or `Zm`, case-sensitively,
followed by zero or more ASCII letters, digits, dots, or hyphens.
`RESOURCE_ID` matches `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`; `REPORT_SHA256`
is 64 lowercase hexadecimal characters. Only
`gene-examples/md/` is listable. All catalog mutation is forbidden.
New manifest and material objects cannot be listed, signed, written, or deleted
through the relay. Their reads remain authenticated and are verified against
the current report and manifest, not accepted as arbitrary object paths.

Tenant-owned `agent_data` objects and content-addressed shared objects retain
their existing authorization rules. The curated catalog is the only
owner-less read exception described here.

## Web Image URL

The Bot contract does not make the Web image route public or complete. When a
Web consumer is separately authorized to use the existing image endpoint, the
canonical reference is:

```markdown
![alt](/api/v1/gene-images/<GENE>/<GENE>_<name>.png)
```

Web forwarding, catalog materialization, browser behavior, staging, and
production acceptance remain external work.

## Producer Rule

A future DeepGenome workflow may target this catalog only after curation
approval and must emit the exact object and image-reference layout above.
Ordinary DeepGenome output is not automatically curated.

## Explicit Material Manifest, Version 1

This is a new offline import contract, not a claim that historical reports
already have a material sidecar. A missing manifest does not establish a
reference excerpt, structure file, or experimental protocol. Never borrow
another gene's example materials or infer associations by title similarity.

The manifest contains exactly these fields:

```json
{
  "schema_version": 1,
  "gene_id": "AT1G01010",
  "report_file": "AT1G01010_result.md",
  "report_sha256": "<64 lowercase hex computed from original report bytes>",
  "reference_count": 2,
  "resources": [
    {
      "id": "protocol",
      "name": "AT1G01010_result-experiments.md",
      "kind": "markdown",
      "markdown_href": "./AT1G01010_result-experiments.md",
      "object_key": "gene-examples/materials/AT1G01010/<report-sha>/protocol.md",
      "media_type": "text/markdown",
      "size_bytes": 123,
      "sha256": "<64 lowercase hex computed from original resource bytes>"
    }
  ],
  "reference_materials": [
    {
      "reference_index": 1,
      "excerpt": "Approved source fragment.",
      "resource_ids": []
    },
    {"reference_index": 2, "excerpt": "", "resource_ids": []}
  ]
}
```

Digest placeholders and the illustrative size above are not valid fixture
values. The compiler computes all sizes, digests, MIME types, and object keys.

- `image` uses `.png` / `image/png` and the retained
  `gene-examples/img/<GENE>/<GENE>_<RESOURCE_ID>.png` layout.
- `cif` uses `.cif` / `chemical/x-cif`; `markdown` uses `.md` /
  `text/markdown`. Their keys include the original report SHA-256.
- `name` is a 1–255 UTF-8 byte basename without slash, backslash, or control
  characters, with the exact extension for its kind.
- `markdown_href` is a 1–2048 UTF-8 byte exact authored-link association. It
  can retain relative `../` components, but it never becomes a path to read
  or a URL to fetch. Absolute associations must start with `/api/`. Reject
  surrounding whitespace, schemes, `//`, backslash,
  controls, query/fragment delimiters, percent escapes, and HTML quotes or
  angle brackets. External bibliography URLs are not material associations.
- Every file is nonempty and at most 8 MiB. PNG needs its signature. Reports,
  Markdown, and CIF need UTF-8 without NUL. Reports reject complete HTML
  document entries (`DOCTYPE html`, `html`, `head`, or `body`) but preserve
  valid Markdown HTML blocks such as a leading `div`. New Markdown and CIF
  materials additionally reject HTML-response entry tags and comments,
  matching HTTP HTML sniffing rather than executing or sanitizing them. CIF's
  first nonempty, non-comment line must start with a nonempty `data_` block
  identifier, allowing an initial UTF-8 BOM without rewriting stored bytes.
  These are format checks, not scientific validation.
- JSON is limited to 6 MiB and nesting depth 8, with no duplicate keys,
  unknown fields, type coercion, nonstandard numeric constants, duplicate
  resource IDs/hrefs, or cross-gene/revision object keys.
- Both resource and reference counts are at most 999. `reference_count` may
  be zero. There are exactly that many ordered `reference_materials` entries,
  numbered 1 through N, including explicit empty excerpts. Duplicate titles
  or repeated excerpts do not cause deduplication or renumbering.
- Each excerpt is at most 64 KiB UTF-8, all excerpts total at most 4 MiB, and
  each slot's `resource_ids` is unique and references only declared resources.
- Readers check exact byte counts and digests before returning registered
  resources. A changed report invalidates its manifest. Private object keys
  and source paths must not be copied into browser DTOs or public errors.

Readers return 404 for missing objects or unregistered/stale resource keys,
409 for malformed or changed manifest bindings, 413 for oversized content,
422 for malformed content, and 502 for upstream unavailability. These codes
do not grant anonymous access or expose internal storage details.

## Offline Import and Check

Use `scripts/build_curated_gene_bundle.py` only with explicitly approved local
sources. It loads a pure contract module without importing the application
bootstrap, credentials, or agent runtime. It performs no network operations,
catalog discovery, or publication.

Approved input has the same `schema_version`, `gene_id`, `report_file`,
`reference_count`, and `reference_materials` fields shown above. Add
`report_source`, a relative POSIX file path within `--source-root`. Each input
resource contains exactly `id`, `name`, `kind`, `markdown_href`, and
`source_file`, another relative file path within that root. Do not include
`report_sha256`, `object_key`, `media_type`, `size_bytes`, or `sha256` in input.

```bash
uv run --no-sync python scripts/build_curated_gene_bundle.py build \
  --input /tmp/approved-gene/input.json \
  --source-root /tmp/approved-gene/source \
  --output-root /tmp/approved-gene/new-bundle

uv run --no-sync python scripts/build_curated_gene_bundle.py check \
  --bundle-root /tmp/approved-gene/new-bundle --gene-id AT1G01010
```

Only explicitly declared regular files are read. Source paths and ancestors
must not be symlinks, even when a symlink points back inside the source root.
Traversal and overlapping source/output roots are rejected. The output parent
must exist; the output itself must be absent or an empty real directory.
Compilation uses a private staging directory and publishes the local bundle
only after every declared input validates, preserving all original report,
PNG, CIF, and Markdown bytes. Failure never leaves a partially compiled final
bundle; existing nonempty output is never overwritten. `check` validates the
existing bundle without writing to it.

The original `runtime/artifact_roles.py` manifest describes ordinary analysis
artifact roles, not curated approval or citation slots. It is not a substitute
for this report-bound contract. Uploading an approved local bundle to a remote
catalog remains a separate operator action; a local check is not evidence of
remote availability or protocol applicability.

## Safe Probes

Use the non-production probes in the [HTTP API runbook](../../ops/http-api-runbook.md#curated-gene-examples-obs-reads).
They demonstrate list, Markdown, image, and forbidden-write behavior without
printing credentials or response bodies into the operator record.
