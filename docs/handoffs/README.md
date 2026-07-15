# Cross-project handoffs

The tracked files in this directory are evidence contracts, examples, and
redacted-record templates for Web, Go gateway, and operations owners. The
three current handoff documents themselves are local-only under
`.codex/handoff/`; they describe the Bot-side contract and the evidence that
must be returned before an external item can be called complete. Nothing here
modifies another repository, a production database, or a deployment.

## Current disposition

| Package                                                                | Status           | Owner                              | Evidence     |
| ---------------------------------------------------------------------- | ---------------- | ---------------------------------- | ------------ |
| Local `.codex/handoff/2026-07-15-deep-genome-web-go-handoff.md`        | External Pending | Phytomni-Web and Go gateway        | Not returned |
| Local `.codex/handoff/2026-07-15-bot-operations-acceptance-handoff.md` | External Pending | Operations and DBA                 | Not returned |
| Local `.codex/handoff/2026-07-15-handoff-disposition-matrix.md`        | External Pending | Bot maintainer with Web/Ops owners | Not returned |

`External Pending` is intentional. Offline tests and local gates prove the
Bot-side contract only; they do not prove a live Web, Go, database, or
production deployment path.

The copyable owner packet index is
[here](evidence/owner-packet-index.md). It maps each `RC-*` acceptance ID to
the external action and the artifact that must be returned. The packet index
does not close an item by itself.

## Release closure packet

The [evidence record contract](evidence/README.md), [owner packet
index](evidence/owner-packet-index.md), and local
`.codex/handoff/2026-07-15-handoff-disposition-matrix.md` are the local 0.1.3
closure packet. The matrix records the current Bot branch, commit, remote-ref
observation, and scoped-gate result; it does not claim that the 63 local
commits have been pushed. Every `RC-*` row remains `External Pending` until an
owner returns a redacted artifact and the release owner links it from the
matrix.

## Evidence rule

An owner may replace `Evidence: Not returned` only with an attached command
transcript, sanitized response, commit or release identifier, and timestamp.
Secrets, customer rows, SQL text, and raw provider responses must not be
attached. A missing transcript stays pending rather than becoming a green
checkmark.

The Bot reference contracts remain the source of truth for route and payload
shape:

- [HTTP API](../reference/http-api.md)
- [A2UI fixtures](../contracts/a2ui/README.md)
- [HTTP operations runbook](../ops/http-api-runbook.md)
