# Agent work trace contracts

This directory is the language-neutral contract authority for provider-backed
user-facing work traces. Bot, Go, and Vue consume the versioned JSON fixtures;
provider payloads and provider identifiers are never fixtures.

`provider-shape-inventory.v1.json` records only field names, finite scalar
types, collection sizes, and behavior relevant to incremental reconciliation.
The source payload inspected on 2026-08-23 was held in memory only. No log
content, task identity, user value, path, URL, command, input, output, or
credential was retained.

`v1/fixtures.json` defines the normalized private observation boundary, the
public presenter boundary, and the opaque trace target. Unknown fields and
unknown record classes fail closed. A provider record becomes public only when
an Agent-specific presenter recognizes its finite semantic code.

`provider-agent-capability-matrix.v1.json` is the rollout truth source. A
feature is advertised only when its state is `supported` and the corresponding
production producer has passed cross-service acceptance.
