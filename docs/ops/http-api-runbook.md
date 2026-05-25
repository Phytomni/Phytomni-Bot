# HTTP API Operations Runbook

This runbook is for operators who deploy `phytomni-api`, issue per-user
API keys, monitor health, rotate local stores, and triage customer tickets.
Endpoint contracts live in [HTTP API](../http-api.md), and all
environment variables live in [Configuration](../configuration.md).

## Scope

Covered:

- Starting and supervising the `phytomni-api` FastAPI service.
- Issuing, listing, and revoking keys with `phytomni-api-key`.
- Health and readiness probes.
- Backup and restore of local SQLite stores.
- Triage for 401, 429, stuck runs, Analyst dedup hits, and startup
  failures.

Out of scope:

- The stdio MCP server, `python -m mcp_server_phytomni.server`.
- Agent business-field semantics. Use [MCP Tool Reference](../mcp-tools.md)
  for tool arguments and demo payloads.
- Building encrypted customer envelopes. Use
  [Deployment and Storage](../deployment.md) for that workflow.

## Service Model

`phytomni-api` and the stdio MCP server are separate processes. They share
the in-process agent packages, but operators can start, stop, and restart
the HTTP service without managing a local MCP client session.

| Process   | Entry point                            | Transport                     | Main consumers                  |
| --------- | -------------------------------------- | ----------------------------- | ------------------------------- |
| HTTP API  | `phytomni-api`                         | TCP, default `127.0.0.1:8080` | Remote clients and integrations |
| stdio MCP | `python -m mcp_server_phytomni.server` | stdin/stdout                  | Local MCP clients               |

Local runtime files:

| Resource         | Default path                        | Purpose                                   |
| ---------------- | ----------------------------------- | ----------------------------------------- |
| API key store    | `.cache/phytomni/api_keys.sqlite`   | Per-user API key hashes and metadata.     |
| Runs/tasks store | `server_tasks.db`                   | Run tracking and child task linkage.      |
| Function cache   | `.cache/phytomni/func_cache.sqlite` | Cached LLM/retrieval/database primitives. |

Defaults are relative to the process working directory. In systemd or
container deployments, pin `WorkingDirectory=` or configure absolute store
paths through the environment.

## Deployment Checklist

1. Install Python 3.12, 3.13, or 3.14.

1. Create a virtual environment and install the package:

   ```bash
   python3.12 -m venv /opt/phytomni/venv
   source /opt/phytomni/venv/bin/activate
   uv pip install -e /path/to/Phytomni-Bot
   ```

1. Provide configuration through one supported path:

   - plaintext `src/mcp_server_phytomni/config/.env` for local/internal
     deployments
   - `.env.encrypted` plus `PHYTOMNI_LICENSE_KEY` for trusted customer
     images

1. Set absolute SQLite paths for production:

   ```bash
   API_KEYS_DB_PATH=/var/lib/phytomni/api_keys.sqlite
   API_TASKS_DB_PATH=/var/lib/phytomni/server_tasks.db
   PHYTOMNI_CACHE_DB=/var/lib/phytomni/func_cache.sqlite
   ```

1. Start the service:

   ```bash
   phytomni-api
   ```

1. Verify liveness and readiness:

   ```bash
   curl -fsS http://127.0.0.1:8080/healthz
   curl -fsS http://127.0.0.1:8080/readyz
   ```

## systemd Example

`/etc/systemd/system/phytomni-api.service`:

```ini
[Unit]
Description=Phytomni HTTP API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=phytomni
Group=phytomni
WorkingDirectory=/var/lib/phytomni
EnvironmentFile=/etc/phytomni/api.env
ExecStart=/opt/phytomni/venv/bin/phytomni-api
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/phytomni

[Install]
WantedBy=multi-user.target
```

Start and follow logs:

```bash
systemctl enable --now phytomni-api
journalctl -u phytomni-api -f
```

Expose the service externally through a TLS-terminating reverse proxy or a
VPN/firewall allow-list. Do not use uvicorn as the public edge.

## API Key Administration

Create a key:

```bash
phytomni-api-key create --user-id alice --name laptop
```

Create a key that expires:

```bash
phytomni-api-key create --user-id alice --name trial --expires-days 30
```

List keys:

```bash
phytomni-api-key list
phytomni-api-key list --user-id alice
```

Revoke by prefix:

```bash
phytomni-api-key revoke --prefix ptm_xxxxxxxx
```

The plaintext key is printed once by `create` and cannot be recovered
later. Clients can send it as either:

```text
Authorization: Bearer ptm_...
X-API-Key: ptm_...
```

Use [CLI Reference](../cli.md) for the complete command reference.

## Endpoint Inventory

| Method   | Path                      | Auth | Operational use                                  |
| -------- | ------------------------- | ---- | ------------------------------------------------ |
| `GET`    | `/healthz`                | no   | Process liveness.                                |
| `GET`    | `/readyz`                 | no   | Store-directory writability check.               |
| `GET`    | `/v1/models`              | yes  | Authenticated liveness and model map check.      |
| `POST`   | `/v1/chat/completions`    | yes  | OpenAI-compatible chat-like agents.              |
| `GET`    | `/v1/agents`              | yes  | Native agent slug discovery.                     |
| `POST`   | `/v1/agents/{agent}/runs` | yes  | Native agent submission.                         |
| `GET`    | `/v1/runs/{run_id}`       | yes  | Owner-scoped run lookup.                         |
| `GET`    | `/v1/runs`                | yes  | Owner-scoped + service-token delegated listing.  |
| `POST`   | `/v1/api-keys`            | svc  | Mint a per-user `ptm_...` API key (service tok). |
| `GET`    | `/v1/api-keys`            | svc  | List per-user keys (metadata only).              |
| `DELETE` | `/v1/api-keys/{prefix}`   | svc  | Revoke the key with the given public prefix.     |

`DataAgent` is a synchronous native run: the HTTP layer returns its result
inline with status `200`.

`GET /v1/runs` accepts these query parameters beyond the basic set:
`user_id=<other>` requires `X-Service-Token` (returns `403` without
it) and lists any tenant's runs; `created_after=<iso-8601>` /
`created_before=<iso-8601>` apply inclusive ISO-8601 date bounds;
`debug=true` keeps the full `result.raw` payload on each row. Each
row carries `dialogue_id` / `query` / `tool_name` / `model` /
`answer` alongside the standard fields, sourced from
`result.formatted.answer`.

## Health Checks

Liveness:

```bash
curl -fsS http://127.0.0.1:8080/healthz
```

Expected body:

```json
{"status":"ok"}
```

Readiness:

```bash
curl -i http://127.0.0.1:8080/readyz
```

`200` means the configured API key and task store directories are writable.
`503` means at least one store directory is not writable. Fix permissions
or repoint the store paths, then restart.

Authenticated smoke:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/models"
```

Expected model ids:

- `phyto-chat`
- `phyto-knowledge`
- `phyto-review`
- `phyto-brief-gene`

## Backup and Restore

Back up SQLite stores with SQLite's `.backup` command:

```bash
mkdir -p "/backup/$(date +%F)"
sqlite3 "$API_KEYS_DB_PATH" ".backup /backup/$(date +%F)/api_keys.sqlite"
sqlite3 "$API_TASKS_DB_PATH" ".backup /backup/$(date +%F)/server_tasks.db"
```

Do not copy a WAL-mode SQLite file directly while the service is running;
the copy may miss uncheckpointed transactions.

Restore:

1. Stop the service.
1. Copy backup files into the configured paths.
1. Start the service.
1. Run `/readyz` and `/v1/models` smoke checks.

The function cache is recoverable and can usually be rebuilt by traffic.
Back it up only when preserving expensive cached remote results matters.

## Restart and Upgrade

Restart:

```bash
systemctl restart phytomni-api
journalctl -u phytomni-api -n 50 -f
```

A restart clears in-memory rate-limit counters. It does not clear API keys,
runs, tasks, or on-disk function-cache rows.

Upgrade one host:

1. Remove the host from the load balancer or set its readiness weight to 0.
1. `systemctl stop phytomni-api`
1. `uv pip install -e /path/to/new/Phytomni-Bot`
1. `systemctl start phytomni-api`
1. Run the health checks above.
1. Return the host to service.

For multi-host deployments, roll one host at a time.

## Triage SOPs

### 401 Unauthorized

Check in order:

1. The customer copied the key incorrectly. Compare the key prefix against
   `phytomni-api-key list --user-id <user>`.
1. The key was revoked. `list` shows `ACTIVE=False`.
1. The key expired.
1. The service and CLI point at different `API_KEYS_DB_PATH` values.

Do not ask the customer to resend full keys through tickets. Prefix plus
user id is enough for store-side checks.

### 429 Rate Limited

Read the `Retry-After` response header and tell the client to back off for
that many seconds.

For sustained legitimate traffic, either raise `API_RATE_LIMIT_PER_MIN`
and restart, or issue additional keys for the same user and let the client
rotate among them.

The limiter is per process. Restarting the service clears the current
counter.

### Run Is Stuck

Ask for the `run_id` from the `202` response, then fetch:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/runs/$RUN_ID"
```

Interpretation:

- `status == "running"`: still in flight. Remote agents can take tens of
  minutes to several hours.
- `status == "failed"`: inspect the `error` field and escalate to
  application engineers.
- `status == "succeeded"` but the customer did not receive it: check
  whether the success row expired. Default successful-run TTL is 24 hours.

If the customer has only a `task_id`, query the task store directly:

```bash
sqlite3 "$API_TASKS_DB_PATH" \
  "SELECT run_id, agent, status FROM tasks WHERE task_id = '$TASK_ID';"
```

### Analyst Returned `id: null`

This is expected when Analyst detects a duplicate submission fingerprint
for an in-flight or succeeded task. The response includes:

```json
{
  "id": null,
  "object": "agent.run",
  "agent": "analyst",
  "status": "running",
  "task_ids": [],
  "result": {
    "dedup_hit": true,
    "task_id": "T-original-task-id"
  }
}
```

The client should poll `result.task_id`. If `id` is `null` without
`result.dedup_hit == true`, retry the request and escalate if it repeats.

### BriefGene Answer Is Generic Or Hits `nogeneid`

Clients sometimes report that `phyto-brief-gene` returns a vague answer
even though their query mentioned a real gene. The root cause is almost
always that the client submitted a research question or a gene symbol
plus species rather than a single canonical locus id, so `BriefGeneAgent`
fell into its `nogeneid` fallback prompt.

Advise the client to set `resolve_gene_id: true` on the request:

```bash
curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  "$HOST/v1/chat/completions" \
  -d '{"model":"phyto-brief-gene","resolve_gene_id":true,"messages":[{"role":"user","content":"What does AT5G42800 do in Arabidopsis?"}]}'

curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  "$HOST/v1/agents/brief_gene/runs" \
  -d '{"arguments":{"user_query":"rice TPR6 function","resolve_gene_id":true}}'
```

The flag is BriefGene-only. Sending it to any other model or slug
returns `400` with `error.message` naming `BriefGene`. Resolver
failures (blank input, empty candidates, non-JSON LLM output) also
return `400` and the response body's `error.message` carries the
resolver reason for ticket triage. On success the response `metadata`
includes `original_query`, `resolved_gene_id`, and `resolve_gene_id: true` so support can confirm which canonical id BriefGene actually saw.

The resolver adds one shared-cache LLM call per unique free-form query,
so heavy unsupervised opt-in does add LLM cost; the `~90d` `phyto_chat`
cache keeps the marginal cost near zero for repeated identical queries.

### Startup Failure

Common failures:

| Error                                | Meaning                                                      | Fix                                                      |
| ------------------------------------ | ------------------------------------------------------------ | -------------------------------------------------------- |
| `SecretEnvelopeError`                | Wrong license key or damaged `.env.encrypted`.               | Verify `PHYTOMNI_LICENSE_KEY` or redeliver the envelope. |
| `RuntimeError` resolving environment | No plaintext `.env`, no encrypted envelope, no testing mode. | Provide one supported config source.                     |
| `PermissionError` on SQLite path     | Store directory is not writable.                             | Fix permissions or configure absolute store paths.       |
| `OSError: [Errno 98]`                | Port already bound.                                          | Free the port or change `API_PORT`.                      |

If `/readyz` returns 200 but authenticated endpoints return 500, inspect
stderr or `journalctl -u phytomni-api` and escalate with the request id from
the response header.
