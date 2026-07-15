# Command Line Reference

Phytomni-Bot installs five console scripts from `pyproject.toml`.
Operational commands that deal with credentials print secrets only when
explicitly documented below.

## `phytomni`

`phytomni` is the bundled stdio MCP client. It starts the MCP server
subprocess, lists tools, and calls one tool with a JSON object.

```bash
phytomni list-tools
phytomni call ChatAgent '{"user_query":"Explain C3 photosynthesis.","obs_file_list":[]}'
```

Use `--server` to point the client at another Python module, Python file,
or JavaScript file:

```bash
phytomni --server mcp_server_phytomni.server list-tools
```

The `call` subcommand prints the formatted answer first. When the
envelope includes structured blocks, it also prints:

- a TSV table for DataAgent `tabular` headers/rows
- a numbered list for cited-tool `references`
- a one-line `task_id` / `output_dir` / `status` / `failures` summary
  for async submit tools

When `formatted.metadata.failures` is non-empty, read those entries for
the per-task `message` (and `task_label` / `kind` when present). The CLI
prints a `failures={count}` token on the metadata line and up to three
capped `label: message` detail lines beneath it. `formatted.answer` often
already carries a human failure sentence for submit-style tools — check
answer first, then metadata.

Research and Design calls may also include an `interop` summary in the
metadata line when `interop_mode` is `auto` or `required`. It contains only
the operator target id, transport kind (`mcp` / `a2a`), capability, status,
and bounded latency. A `degraded_interop=true` token means `auto` could not
obtain external evidence and continued with the local Analyst path; in
`required` mode an unavailable or failed peer is reported as a failed
submission. Use the same JSON controls in the CLI call as on MCP/HTTP:

```bash
phytomni call InSilicoResearchAgent \
  '{"user_query":"Summarize the paper.","data_list":{},"obs_file_list":[],"interop_mode":"auto","interop_targets":["mcp-peer"]}'
```

DeepGenome (and other agents that continue work after returning a
`task_id`) need a long-lived process: run `phytomni-api` or keep an MCP
server session open, then poll with `GetTaskStatus`. A one-shot
`phytomni call` starts the server as a subprocess and exits after the
tool returns, which cancels in-process background workflows.

### HTTP asynchronous runs

The HTTP run commands use the long-lived `phytomni-api` service. Set the API
base URL and the per-user API key in the environment; the key is deliberately
not accepted as a command-line option:

```bash
export PHYTOMNI_API_URL=http://127.0.0.1:8080
export PHYTOMNI_API_KEY=ptm_...
```

The `--api-url` option overrides `PHYTOMNI_API_URL` for one invocation:

```text
phytomni [--api-url URL] submit <agent> '<arguments-json>'
phytomni [--api-url URL] status <run_id>
phytomni [--api-url URL] follow <run_id> [--poll-interval 5.0] [--wait-timeout 3600.0]
```

`submit` writes only the accepted run id to stdout. Accepted task ids and the
initial status go to stderr. `status` and `follow` write the best available
Markdown to stdout, choosing `final_report`, then `intermediate_report`, and
finally a status line. Their one-line status/progress and sanitized
`degraded_reason` metadata goes to stderr, so stdout remains safe to pipe into
a Markdown file. A failed run still prints its last intermediate report.

Exit codes are **0 success, 1 task failure, 2 client error, 3 local timeout**.
`follow` uses a local monotonic deadline, does not cancel the remote run, and
prints progress only when the `(status, report_revision)` pair changes. The
HTTP-backed CLI is a reader of the API-owned snapshot: it chooses
`final_report`, then `intermediate_report`, and never polls child task ids
directly. A restart of the API process does not resume after process restart;
use the persisted local snapshot for reads and the operator recovery procedure
for orphaned work. This is an in-process coordinator boundary, not a durable
worker guarantee.

The external live-test and remote-ref evidence sequence is documented in the
[live release acceptance packet](../handoffs/evidence/live-release-acceptance.md).
It is separate from the local CLI smoke and must be returned by an authorized
owner before release closure.

For full response models, use `mcp_client_phytomni.client.PhytomniMcpClient`
from Python.

## `phytomni-api`

`phytomni-api` starts the authenticated FastAPI service.

```bash
phytomni-api
```

It exposes no CLI flags. Bind host, port, stores, rate limit, and run TTLs
come from environment variables documented in
[Configuration](configuration.md). Endpoint behavior is documented in
[HTTP API](http-api.md), and operational procedures live in
[HTTP API Operations Runbook](../ops/http-api-runbook.md).

## `phytomni-api-key`

`phytomni-api-key` manages the per-user HTTP API key store.

```bash
phytomni-api-key create --user-id alice --name laptop
phytomni-api-key create --user-id alice --name short-lived --expires-days 30
phytomni-api-key create --user-id cust --scope relay:llm --scope relay:retrieve
phytomni-api-key list
phytomni-api-key list --user-id alice
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

`create` prints the plaintext key exactly once. Store it immediately; the
database keeps only a salted PBKDF2-HMAC-SHA256 hash and the plaintext is
not recoverable.

`--scope` (repeatable) restricts the key to specific services. A
scope-less key keeps full access to the agent routes, but the relay
routes (`/v1/relay/*`) require an explicit `relay:<service>` (or
`relay:*`) scope and deny scope-less keys. Use it to issue a relay
customer key limited to the services it may reach, e.g.
`--scope relay:llm` for model calls or `--scope relay:obs` for object
storage (the OBS relay confines each `relay:obs` key to its own tenant
namespace).

`list` prints non-secret metadata only, including each key's scopes.
`revoke` disables an active key by its public prefix.

## `phytomni-cache`

`phytomni-cache` manages the local SQLite-backed function cache.

```bash
phytomni-cache stats
phytomni-cache purge
phytomni-cache purge --func-id agents.chat.service:run_phyto_chat_cached
phytomni-cache reexpire --ttl 7776000
phytomni-cache reexpire --permanent --func-id agents.data.nl2sql:_execute_nl2sql_cached
phytomni-cache purge-expired
```

Use `--db-path` before the subcommand to operate on a non-default cache
database:

```bash
phytomni-cache --db-path /var/lib/phytomni/func_cache.sqlite stats
```

The default cache path is `PHYTOMNI_CACHE_DB` when set, otherwise
`.cache/phytomni/func_cache.sqlite` relative to the current working
directory.

## `phytomni-task-db`

`phytomni-task-db` prepares a DeepGenome task database for an older binary
that cannot read the child tables. It requires an explicit existing SQLite
file, creates a sibling backup through SQLite's backup API, verifies both
databases with `PRAGMA integrity_check`, and removes remote-task and section
rows in one transaction:

```bash
phytomni-task-db prepare-deep-genome-rollback --db /var/lib/phytomni/server_tasks.db
```

The command refuses persisted nonterminal DeepGenome work with exit code `2`.
After the service has been stopped and that loss is acknowledged, pass
`--mark-nonterminal-failed`; this records a fixed failure reason, clears any
stale final report, preserves the intermediate report, and then removes the
child rows:

```bash
phytomni-task-db prepare-deep-genome-rollback \
  --db /var/lib/phytomni/server_tasks.db \
  --mark-nonterminal-failed
```

Successful preparation returns exit code `0` and prints only database paths
and deletion counts. Invalid paths or SQLite failures return exit code `1`.
