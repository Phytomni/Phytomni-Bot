# External acceptance evidence records

This directory contains copyable, redacted evidence templates for the 0.1.3
release. The files describe what Web, Go, Operations, DBA, GitHub, and the
release owner must return; they do not execute those actions.

## Required record

Start with [`record-template.json`](record-template.json). Replace only the
angle-bracketed synthetic values and keep the record free of secrets,
customer rows, private endpoints, SQL text, and raw provider responses.

Required keys are:

| Key                  | Requirement                                                                    |
| -------------------- | ------------------------------------------------------------------------------ |
| `acceptance_id`      | One `RC-*` identifier from the release matrix.                                 |
| `owner`              | Team or role that performed the check.                                         |
| `state`              | `Prepared`, `External Pending`, `Evidence Returned`, `Accepted`, or `Blocked`. |
| `commit`             | Exact Bot or consumer commit under test, or an explicit non-code label.        |
| `environment`        | Sanitized environment name, never a DSN or secret.                             |
| `command_or_request` | Redacted command, route, or test name.                                         |
| `expected`           | Observable acceptance condition.                                               |
| `observed`           | Redacted result, including request IDs when available.                         |
| `artifact`           | Relative path, CI URL, change record, or `not-returned`.                       |
| `redaction`          | What was removed or the statement `none-required`.                             |
| `recorded_at`        | UTC ISO-8601 timestamp.                                                        |

## Review rules

1. A local plan checkbox, mock response, or screenshot without a request ID is
   not evidence.
1. A `Closed` state requires a reviewed artifact and a link from the tracked
   disposition matrix; the template intentionally does not use `Closed`.
1. A failed check gets a new record and keeps the previous record immutable.
1. Any credential-shaped value, customer data, or private endpoint rejects the
   record until it is redacted.
1. Missing owner output remains `External Pending`.

Evidence records are release artifacts only after the owner returns them and a
reviewer links them from the local-only
`.codex/handoff/2026-07-15-handoff-disposition-matrix.md`.
