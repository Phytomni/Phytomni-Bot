# AI Agent Feature Templates

Use the smallest template matching the lifecycle. Every proposal identifies
catalog fields, owner, request/result contract, failure semantics, tests,
deployment order, rollback, and unresolved external evidence.

## Synchronous

- Catalog: tool, slug, schema, handler, result contract, attachments.
- Flow: validated request -> shared handler -> formatted terminal result.
- Tests: MCP/HTTP parity, validation, formatted result, failure redaction.

## Streaming

- Include every synchronous item plus model allowlist and stream primitive.
- Define setup failure, first-byte boundary, cancellation, terminal settlement,
  and stored-answer limits.
- Tests: blocking/stream parity, ordered events, cancellation before settlement.

## Resumable

- Include stable run/owner/context identities and allowed interrupt payloads.
- Define `INPUT_REQUIRED`, idempotent resume, stale/conflicting input, timeout,
  cancellation, and Web history projection.
- Tests: pause, owner isolation, resume once, duplicate resume, terminal result.

## Asynchronous

- Include durable intent/outbox, idempotency key, run/task correlation, degraded
  tracking, reconciliation, artifacts, terminal shaping, retry, and cancellation.
- Tests: submit, dedup, restart recovery, poll, partial/terminal failure,
  cancellation, delivery authorization.

## Cross-repository additions

Bot publishes and verifies the additive contract first. Web consumes only a
finite validated projection with default-off compatibility. Use the handoff
template in `Phytomni-Web/docs/development/` and never treat local compatibility
as deployment or activation evidence.
