# Command Line Reference

Phytomni-Bot installs four console scripts from `pyproject.toml`.
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

DeepGenome (and other agents that continue work after returning a
`task_id`) need a long-lived process: run `phytomni-api` or keep an MCP
server session open, then poll with `GetTaskStatus`. A one-shot
`phytomni call` starts the server as a subprocess and exits after the
tool returns, which cancels in-process background workflows.

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
