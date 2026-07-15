# Cross-project handoffs

These handoffs are tracked repository artifacts for Web, Go gateway, and
operations owners. They describe the Bot-side contract and the evidence that
must be returned before an external item can be called complete. They do not
modify another repository, a production database, or a deployment.

## Current disposition

| Package                                                                       | Status           | Owner                              | Evidence                                            |
| ----------------------------------------------------------------------------- | ---------------- | ---------------------------------- | --------------------------------------------------- |
| [Web and Go DeepGenome integration](2026-07-15-deep-genome-web-go-handoff.md) | External Pending | Phytomni-Web and Go gateway        | Not returned                                        |
| Operations acceptance                                                         | External Pending | Operations and DBA                 | Not returned; document follows in the next delivery |
| Original handoff disposition matrix                                           | External Pending | Bot maintainer with Web/Ops owners | Not returned; document follows in the next delivery |

`External Pending` is intentional. Offline tests and local gates prove the
Bot-side contract only; they do not prove a live Web, Go, database, or
production deployment path.

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
