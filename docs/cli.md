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

The `call` subcommand prints the formatted answer text to stdout. For
full response models, use `mcp_client_phytomni.client.PhytomniMcpClient`
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
[HTTP API Operations Runbook](ops/http-api-runbook.md).

## `phytomni-api-key`

`phytomni-api-key` manages the per-user HTTP API key store.

```bash
phytomni-api-key create --user-id alice --name laptop
phytomni-api-key create --user-id alice --name short-lived --expires-days 30
phytomni-api-key list
phytomni-api-key list --user-id alice
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

`create` prints the plaintext key exactly once. Store it immediately; the
database keeps only a salted PBKDF2-HMAC-SHA256 hash and the plaintext is
not recoverable.

`list` prints non-secret metadata only. `revoke` disables an active key by
its public prefix.

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
