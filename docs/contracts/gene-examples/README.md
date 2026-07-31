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
```

`GENE` is case-sensitive and starts with `AT`, `GLYMA`, `Os`, `Traes`, or
`Zm`. Markdown and image objects are readable. Only
`gene-examples/md/` is listable. All catalog mutation is forbidden.

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

## Safe Probes

Use the non-production probes in the [HTTP API runbook](../../ops/http-api-runbook.md#curated-gene-examples-obs-reads).
They demonstrate list, Markdown, image, and forbidden-write behavior without
printing credentials or response bodies into the operator record.
