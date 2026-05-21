# Phytomni HTTP API Operations Runbook

This document is for operations engineers responsible for deploying the
service, issuing API keys, monitoring endpoint health, and triaging customer
tickets. It is self-contained: nothing outside this file is required to run,
probe, or recover the HTTP API.

## 0. Scope

**Covered**:

- The `phytomni-api` HTTP service (FastAPI on uvicorn)
- The `phytomni-api-key` admin CLI (issue, list, revoke)
- Service-level health checks (`/healthz` and `/readyz`) and endpoint-level
  liveness verification
- Error codes, the unified error envelope, and rate-limit responses
- Triage SOPs for the recurring customer questions (401, 429, "my run is
  stuck", "Analyst returned `id: null`", startup failures)

**Out of scope**:

- The stdio MCP server (`python -m mcp_server_phytomni.server`) — that is a
  separate process, owned by application engineers / integrators, and does
  not listen on a TCP port
- Signing of `.env.encrypted` envelopes — that is performed by the image
  distribution engineer; this runbook assumes either a plaintext `.env` or
  an `.env.encrypted` plus license key is already in place
- Agent business-field semantics (what to put in `goal_description`, etc.)
  — see the Available MCP Tools section of `README.md`

## 1. Service overview

### 1.1 Process model

`phytomni-api` (HTTP API) and `python -m mcp_server_phytomni.server` (stdio
MCP) are **two fully independent processes**. They share the in-process
agent layer (`agents/` package) but do not depend on each other and can be
started, stopped, and restarted independently:

| Process   | Entry point                            | Listens on                     | Consumers                                              |
| --------- | -------------------------------------- | ------------------------------ | ------------------------------------------------------ |
| HTTP API  | `phytomni-api`                         | TCP (default `127.0.0.1:8080`) | Remote clients, third-party integrations, this runbook |
| stdio MCP | `python -m mcp_server_phytomni.server` | stdin/stdout                   | Claude Desktop, local MCP clients                      |

Operations only owns the HTTP API; the stdio process is typically started
by integrators on the customer machine.

### 1.2 Bind host and port

Default `127.0.0.1:8080` — loopback only. To expose the service externally:

- **Preferred**: put a reverse proxy in front (nginx / Caddy / Traefik /
  Cloudflare Tunnel) that terminates TLS and forwards to `127.0.0.1:8080`
- **Acceptable**: set `API_HOST=0.0.0.0`, but only behind a firewall
  allow-list or a VPN — otherwise the API key store and run registry are
  exposed to the public internet
- Do not use uvicorn as a public edge proxy — it does not handle TLS
  redirects, HTTP/2 multiplexing, or connection reuse

### 1.3 Local resources the service touches

The service writes the following files at runtime. Operations must ensure
the directories are writable and that these files are part of the backup
policy:

| Resource        | Default path (relative to CWD)          | Purpose                                  |
| --------------- | --------------------------------------- | ---------------------------------------- |
| API keys DB     | `.cache/phytomni/api_keys.sqlite`       | Per-user API key hash + salt + metadata  |
| Tasks / runs DB | `server_tasks.db`                       | Run tracking, status, child task linkage |
| Function caches | `.cache/phytomni/` (other sqlite files) | LLM completion + retrieval caches        |

These default paths are **relative to the working directory** of the
process, not to `~/.cache`. In a systemd unit, pin them via either
`WorkingDirectory=` or absolute `API_KEYS_DB_PATH` / `API_TASKS_DB_PATH`,
otherwise launches from different shells will write to different on-disk
locations.

The obsfs mount at `/obs/phytomni` is owned by the infrastructure layer.
When the mount is missing the agents fall back to the OBS SDK directly,
losing a small performance advantage but staying fully functional —
operations does not need to intervene in obsfs.

## 2. Deployment

### 2.1 Prerequisites

- Python 3.12 / 3.13 / 3.14 (`pyproject.toml` constrains `>=3.12,<3.15`)
- `pip` or [`uv`](https://github.com/astral-sh/uv) (uv is preferred)
- Write access to the working directory or to the paths pointed at by
  `API_KEYS_DB_PATH` / `API_TASKS_DB_PATH`
- Network reachability to: the configured LLM provider, OBS, and the
  Phytomni backend services (see `.env.example` for the exact hostnames)

### 2.2 Installation

```bash
# Recommended: venv + uv
python3.12 -m venv /opt/phytomni/venv
source /opt/phytomni/venv/bin/activate
uv pip install -e /path/to/Phytomni-Bot
```

Plain `pip` works too:

```bash
python3.12 -m venv /opt/phytomni/venv
source /opt/phytomni/venv/bin/activate
pip install -e /path/to/Phytomni-Bot
```

The `[dev]` and `[demo]` extras are for local development and demo-data
regeneration; they are not needed in production.

### 2.3 .env and license preparation

At startup the configuration is resolved in this order:

1. `PHYTOMNI_TESTING=1` (**never enable in production**; injects dummy
   secrets)
1. A license key, from one of:
   - The `PHYTOMNI_LICENSE_KEY` environment variable
   - The file `<install>/src/mcp_server_phytomni/config/.license_key`
1. The encrypted envelope at
   `<install>/src/mcp_server_phytomni/config/.env.encrypted`
1. Plaintext `<install>/src/mcp_server_phytomni/config/.env`

For image distributions the shipped image only contains `.env.encrypted`;
operations supplies the license key on the host (the environment variable
wins over the file).

If the license is wrong the process aborts at startup with
`SecretEnvelopeError`, typical message:

```text
SecretEnvelopeError: wrong license key or corrupted file
```

When you see that line, the fix is to correct `PHYTOMNI_LICENSE_KEY` or
the contents of `.license_key`.

### 2.4 Start command

```bash
phytomni-api
```

`phytomni-api` exposes no CLI flags; every configurable knob comes from
environment variables (see section 8). Minimum self-check:

```bash
curl -s http://127.0.0.1:8080/healthz
# expected: {"status":"ok"}
```

### 2.5 systemd unit example

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
# Hardening — strongly recommended
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/phytomni

[Install]
WantedBy=multi-user.target
```

`/etc/phytomni/api.env`:

```bash
API_HOST=127.0.0.1
API_PORT=8080
API_KEYS_DB_PATH=/var/lib/phytomni/api_keys.sqlite
API_TASKS_DB_PATH=/var/lib/phytomni/server_tasks.db
API_RATE_LIMIT_PER_MIN=120
PHYTOMNI_LICENSE_KEY=<obtain from vendor>
```

Enable and tail logs:

```bash
systemctl daemon-reload
systemctl enable --now phytomni-api
journalctl -u phytomni-api -f
```

### 2.6 Multi-worker and reverse-proxy notes

uvicorn runs a single worker by default. The rate limiter (section 7)
keeps state in process memory, so **multiple workers make the limit
ineffective**: each worker counts independently and total throughput
becomes `workers × limit`.

Scaling options:

- **Preferred**: rate-limit at the reverse-proxy layer (nginx
  `limit_req`, Cloudflare Rate Limiting), treat the in-process limiter
  as a fallback
- **Acceptable**: keep one worker per host, scale horizontally, and let
  the reverse proxy load-balance
- Do not pass `--workers N` to `phytomni-api` (no such flag exists today,
  and it would silently break the limiter)

## 3. API key administration (`phytomni-api-key`)

### 3.1 Command reference

```bash
phytomni-api-key create --user-id <user> [--name <label>] [--expires-days <N>]
phytomni-api-key list   [--user-id <user>]
phytomni-api-key revoke --prefix <prefix>
```

All subcommands read `API_KEYS_DB_PATH`. Make sure the CLI sees the same
configuration as the running service — the simplest way is to source the
same `EnvironmentFile` used by the systemd unit.

### 3.2 Issuing a key (`create`)

```bash
phytomni-api-key create --user-id alice --name laptop
```

Typical output:

```text
API key created. Store it now; it is shown only once and cannot be recovered.
  key:    ptm_your_real_api_key_will_appear_here_example
  prefix: ptm_your_real
  user:   alice
  name:   laptop
```

Hard rules:

- The plaintext `key` is printed exactly once at issuance; only the salt
  and hash are stored
- Operations cannot recover a forgotten key — revoke and reissue
- `--expires-days N` is optional; without it the key never expires

Deliver the plaintext key over a secure channel (password manager,
encrypted email, end-to-end IM). Never paste it into a ticket comment,
shared notes, or chat history.

### 3.3 Listing keys (`list`)

```bash
phytomni-api-key list
```

Columns:

```text
PREFIX        USER            ACTIVE  CREATED_AT                        NAME
ptm_yourkey1  alice           True    2026-05-21 02:14:33.412+00:00     laptop
ptm_yourkey2  bob             False   2026-04-30 09:11:02.001+00:00     ci-runner
```

- `PREFIX` is the first 12 characters of the key — the handle used for
  `revoke`
- `ACTIVE` is `False` when the key is revoked or past its `expires_at`
- The plaintext key is never recoverable from this view

Use `--user-id` to filter by user.

### 3.4 Revoking a key (`revoke`)

```bash
phytomni-api-key revoke --prefix ptm_yourkey1
```

`revoked` means success. `no active key with that prefix` means the key
either does not exist, was already revoked, or the prefix was mistyped.

Revocation is a soft delete (the `active` column flips to false); the row
remains for audit purposes.

### 3.5 Storage paths and backups

- The key store is the SQLite file at `API_KEYS_DB_PATH` (default
  `.cache/phytomni/api_keys.sqlite` relative to the working directory)
- SQLite runs in WAL mode. Back up by either (a) copying the `.sqlite`,
  `.sqlite-wal`, and `.sqlite-shm` files together, or (b) using
  `sqlite3 <path> .backup <target>` for a transaction-safe hot copy
- Recommended cadence: daily backup with seven-day retention. Do not
  `rsync` the bare `.sqlite` — that may capture an unflushed transaction

### 3.6 Key format and auth headers

- Plaintext form: `ptm_` prefix + URL-safe base64 random payload
- Hash: PBKDF2-HMAC-SHA256, 200,000 iterations, with a 16-byte random
  salt per key
- Comparison: `secrets.compare_digest()` for timing-safe equality

Two header forms are accepted, with `Authorization` taking precedence:

```http
Authorization: Bearer ptm_your_real_api_key_example_only
```

or:

```http
X-API-Key: ptm_your_real_api_key_example_only
```

When both headers are present only `Authorization` is consulted. A
request with no credentials returns 401, with `WWW-Authenticate: Bearer`
on the response.

## 4. Health checks

### 4.1 `/healthz` — liveness only

```bash
curl -s http://127.0.0.1:8080/healthz
```

- Status code: **always** 200
- Body: `{"status":"ok"}`
- Checks: process is alive and the ASGI loop is responsive; **no**
  dependency checks
- Use case: Kubernetes liveness probe, load-balancer "is the process
  alive" check

### 4.2 `/readyz` — readiness with dependency checks

```bash
curl -i http://127.0.0.1:8080/readyz
```

Healthy (200):

```json
{
  "status": "ok",
  "checks": {
    "api_keys_db": true,
    "tasks_db": true
  }
}
```

Degraded (503, unified error envelope):

```json
{
  "error": {
    "type": "unavailable",
    "code": 503,
    "message": "one or more local stores are not writable",
    "request_id": "request-20260521-..."
  }
}
```

What is checked: the parent directories of `API_KEYS_DB_PATH` and
`API_TASKS_DB_PATH` are writable. The probe never creates files or
tables, so repeated polling is side-effect free.

Use case: Kubernetes readiness probe, CI/CD smoke tests, the last gate
before flipping traffic to a new release.

### 4.3 `/v1/models` — authenticated liveness

`/readyz` does not validate API keys, so it can only confirm "the
process is up and accepting requests". To additionally verify
"authentication works and a specific key is valid", probe `/v1/models`:

```bash
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8080/v1/models | jq .
```

Expected response:

```json
{
  "object": "list",
  "data": [
    {"id": "phyto-chat",       "object": "model", "owned_by": "phytomni"},
    {"id": "phyto-knowledge",  "object": "model", "owned_by": "phytomni"},
    {"id": "phyto-review",     "object": "model", "owned_by": "phytomni"},
    {"id": "phyto-brief-gene", "object": "model", "owned_by": "phytomni"}
  ]
}
```

Any 4xx or 5xx response on this endpoint is an authentication or service
problem — diagnose using section 6.

### 4.4 End-to-end smoke script

Run once after every deploy:

```bash
#!/usr/bin/env bash
set -euo pipefail
HOST=${HOST:-http://127.0.0.1:8080}
KEY=${KEY:?need KEY=ptm_...}

echo "1) liveness"
curl -fsS "$HOST/healthz" | jq -e '.status == "ok"'

echo "2) readiness"
curl -fsS "$HOST/readyz"  | jq -e '.status == "ok"'

echo "3) auth + model list"
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/models" \
  | jq -e '.data | length >= 4'

echo "4) minimal chat round-trip"
curl -fsS -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"phyto-chat","messages":[{"role":"user","content":"ping"}]}' \
  "$HOST/v1/chat/completions" \
  | jq -e '.choices[0].message.content | length > 0'

echo "all checks passed"
```

A non-zero exit means deploy failure — block the rollout.

## 5. Endpoint reference

### 5.1 Public endpoints (no auth)

| Method | Path       | Status codes | Description                                |
| ------ | ---------- | ------------ | ------------------------------------------ |
| GET    | `/healthz` | 200          | Liveness (always 200)                      |
| GET    | `/readyz`  | 200 / 503    | Readiness; checks local SQLite directories |

### 5.2 OpenAI-compatible endpoints

| Method | Path                   | Description                      |
| ------ | ---------------------- | -------------------------------- |
| GET    | `/v1/models`           | Lists the four chat-style models |
| GET    | `/v1/agents`           | Lists all ten native agent slugs |
| POST   | `/v1/chat/completions` | OpenAI ChatCompletion shape      |

The `model` field of `/v1/chat/completions` selects the underlying agent:

| model              | Backing agent  | Accepts `obs_file_list`?                      |
| ------------------ | -------------- | --------------------------------------------- |
| `phyto-chat`       | ChatAgent      | yes                                           |
| `phyto-knowledge`  | KnowledgeAgent | yes                                           |
| `phyto-review`     | ReviewAgent    | yes                                           |
| `phyto-brief-gene` | BriefGeneAgent | no (accepts only a gene id in `user` content) |

`stream: true` is **not** supported and is rejected with 400. Standard
OpenAI clients work as long as they leave `stream` at its default
(`false`).

### 5.3 Agent run submission

```http
POST /v1/agents/{slug}/runs
```

Allowed slugs and their behavior:

| slug          | Backing agent         | HTTP status | Type                                                                      |
| ------------- | --------------------- | ----------- | ------------------------------------------------------------------------- |
| `chat`        | ChatAgent             | 200         | sync (returns the result in one call)                                     |
| `knowledge`   | KnowledgeAgent        | 200         | sync                                                                      |
| `data`        | DataAgent             | 200         | sync (README calls it a "long task" but the HTTP layer returns it inline) |
| `review`      | ReviewAgent           | 200         | sync                                                                      |
| `brief_gene`  | BriefGeneAgent        | 200         | sync                                                                      |
| `analyst`     | AnalystAgent          | 202         | async (remote task; client must poll)                                     |
| `deep_genome` | DeepGenomeAgent       | 202         | async                                                                     |
| `research`    | InSilicoResearchAgent | 202         | async                                                                     |
| `design`      | DigitalDesignAgent    | 202         | async                                                                     |
| `network`     | GeneNetworkAgent      | 202         | async                                                                     |

Request body:

```json
{"arguments": { /* per-agent kwargs; see README for field semantics */ }}
```

Sync response body (200):

```json
{
  "id": "run-20260521-...",
  "object": "agent.run",
  "agent": "chat",
  "status": "succeeded",
  "task_ids": [],
  "result": { /* standard FormattedToolResult */ }
}
```

Async response body (202):

```json
{
  "id": "run-20260521-...",
  "object": "agent.run",
  "agent": "analyst",
  "status": "running",
  "task_ids": ["T-abc...", "T-def..."],
  "result": { /* submission acknowledgement */ }
}
```

An unknown slug returns 404 with the unified error envelope.

### 5.4 Run tracking

```http
GET /v1/runs/{run_id}
GET /v1/runs?status=&agent=&origin=&limit=&offset=
```

Both endpoints are **owner-scoped** by the authenticated user: user A
cannot read user B's runs. Foreign or unknown runs return 404 without
distinguishing the two cases.

Single-run response fields:

```json
{
  "run_id": "run-...",
  "agent": "analyst",
  "origin": "remote",
  "user_id": "alice",
  "status": "running | succeeded | failed | ...",
  "result": { ... },
  "error": null,
  "created_at": "2026-05-21T...",
  "updated_at": "2026-05-21T...",
  "expires_at": "2026-05-22T...",
  "task_ids": ["T-..."]
}
```

`GET /v1/runs/{id}` reconciles the run against the backend task status
on every call, so the query itself doubles as a refresh.

TTL: successful runs live 24 hours; failed runs live 7 days. A lazy GC
runs after every write and list, so operations rarely needs to vacuum
manually.

### 5.5 Minimal curl for all ten agents

> Placeholders: `$HOST=http://127.0.0.1:8080`, `$KEY=ptm_...`. The OBS
> paths in `data_list` / `obs_file_list` are illustrative; the demo paths
> are usable for connectivity tests, or substitute real customer paths.

#### chat (sync, 200)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"ping","obs_file_list":[]}}' \
  "$HOST/v1/agents/chat/runs"
```

#### knowledge (sync, 200)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"drought tolerance in wheat","obs_file_list":[]}}' \
  "$HOST/v1/agents/knowledge/runs"
```

#### data (sync, 200)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"orthologs of Os01g0177400 in wheat"}}' \
  "$HOST/v1/agents/data/runs"
```

#### review (sync, 200)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"sorghum drought review","obs_file_list":[]}}' \
  "$HOST/v1/agents/review/runs"
```

#### brief_gene (sync, 200)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"user_query":"Os01g0177400"}}' \
  "$HOST/v1/agents/brief_gene/runs"
```

#### analyst (async, 202 → poll `/v1/runs/{id}`)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{
        "arguments": {
          "goal_description": "ATAC-seq peak calling for two rice replicates",
          "data_list": {
            "/obs/phytomni/demo/sequences/sample_rep1.fastq.gz": "rep1 desc",
            "/obs/phytomni/demo/sequences/sample_rep2.fastq.gz": "rep2 desc"
          },
          "obs_file_list": []
        }
      }' \
  "$HOST/v1/agents/analyst/runs"
```

#### deep_genome (async, 202)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"gene_id":"Os01g0177400","species_code":"osa"}}' \
  "$HOST/v1/agents/deep_genome/runs"
```

#### research (async, 202)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{
        "arguments": {
          "user_query": "decompose plant-science brief",
          "data_list": {
            "/obs/phytomni/demo/docs/sample_metadata.xlsx": "metadata desc",
            "/obs/phytomni/demo/sequences/arabidopsis_sample.fasta": "seq desc"
          },
          "obs_file_list": ["/obs/phytomni/demo/docs/plant_science_brief.pdf"]
        }
      }' \
  "$HOST/v1/agents/research/runs"
```

#### design (async, 202)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"gene_id":"Os01g0177400","species":"oryza sativa","obs_file_list":[]}}' \
  "$HOST/v1/agents/design/runs"
```

#### network (async, 202)

```bash
curl -fsS -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"arguments":{"species":"oryza sativa","to_id":"TO:0000207","obs_file_list":[]}}' \
  "$HOST/v1/agents/network/runs"
```

After receiving 202, read `id` (the run id) and poll:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/runs/$RUN_ID"
```

until `status` is `succeeded` or `failed`.

## 6. Error codes and the unified error envelope

### 6.1 Envelope shape

Every 4xx and 5xx response shares one JSON shape:

```json
{
  "error": {
    "type":    "<machine-readable slug>",
    "code":    <http_status>,
    "message": "<human-readable explanation>",
    "request_id": "<X-Request-Id>"
  }
}
```

Each status code has a stable `type` slug:

| code | type                   | Trigger                                                                                    |
| ---- | ---------------------- | ------------------------------------------------------------------------------------------ |
| 400  | `bad_request`          | Invalid client payload (e.g. `stream: true`, `obs_file_list` not accepted by model)        |
| 401  | `unauthorized`         | Missing / wrong / revoked / expired API key                                                |
| 403  | `forbidden`            | Currently unused (reserved)                                                                |
| 404  | `not_found`            | Unknown model, unknown agent slug, or run that does not exist (or belongs to another user) |
| 409  | `conflict`             | Currently unused (reserved)                                                                |
| 422  | `unprocessable_entity` | Pydantic request validation failure (missing field, wrong type)                            |
| 429  | `rate_limited`         | Rate limit exceeded; response carries `Retry-After`                                        |
| 500  | `internal_error`       | Uncaught exception (including agent crashes)                                               |
| 503  | `unavailable`          | `/readyz` failure (local SQLite directory not writable)                                    |

### 6.2 Reproducing each error code

For end-to-end verification operations can trigger each code on demand:

| code | How to reproduce                                                                            |
| ---- | ------------------------------------------------------------------------------------------- |
| 400  | `curl -d '{"model":"phyto-chat","messages":[{"role":"user","content":"x"}],"stream":true}'` |
| 401  | Request `/v1/models` with no authentication header                                          |
| 404  | `POST /v1/agents/no_such_slug/runs` or `GET /v1/runs/run-not-exist`                         |
| 422  | `POST /v1/chat/completions` without a `messages` field                                      |
| 429  | Burst the same key above `API_RATE_LIMIT_PER_MIN` requests within 60 seconds                |
| 503  | Temporarily `chmod 000` the API keys DB parent directory, hit `/readyz`, restore the mode   |

### 6.3 Using `request_id` to correlate logs

Every response carries an `X-Request-Id` header. When an error occurs the
same id appears in `error.request_id` inside the body. Ask the customer
to include this id in their ticket — `journalctl -u phytomni-api | grep <id>` then pinpoints the exact request in the service log.

The server generates a fresh `X-Request-Id` for every request; the
current version does **not** honor a client-supplied `X-Request-Id`. For
cross-service tracing, record the mapping between your upstream trace id
and the server-generated id in the reverse proxy layer or in the client
SDK.

## 7. Rate limiting

### 7.1 Algorithm

- Algorithm: sliding window (rolling hit list)
- Window width: 60 seconds (hardcoded; only test code can override it)
- Bucket: the **API key prefix** (one bucket per key; different keys are
  independent)
- Storage: an in-process `dict[prefix, list[timestamps]]` — single
  process only

### 7.2 Configuration

| Variable                 | Default | Meaning                                 |
| ------------------------ | ------- | --------------------------------------- |
| `API_RATE_LIMIT_PER_MIN` | 120     | Allowed requests per key per 60 seconds |

Setting the value to `0` or a negative number disables limiting.

### 7.3 Rate-limit response

When the limit trips:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 37
Content-Type: application/json

{"error":{"type":"rate_limited","code":429,"message":"rate limit exceeded","request_id":"request-..."}}
```

`Retry-After` is an integer number of seconds, minimum 1. Clients should
back off until that wall-clock interval has elapsed before retrying.

### 7.4 Multi-worker caveat

Because the counter lives in process memory, multi-worker deployments
would double, triple, etc. the effective limit. The current
`phytomni-api` runs a single worker by default (no `--workers` flag), so
this is moot. If you ever switch to `gunicorn -w N` or
`docker compose replicas: N`, layer a real rate limiter at the reverse
proxy.

## 8. Configuration reference

All environment variables the service reads:

| Variable                                     | Default                                              | Sensitive? | Purpose                                           |
| -------------------------------------------- | ---------------------------------------------------- | ---------- | ------------------------------------------------- |
| `API_HOST`                                   | `127.0.0.1`                                          | no         | uvicorn bind address                              |
| `API_PORT`                                   | `8080`                                               | no         | uvicorn bind port                                 |
| `API_KEYS_DB_PATH` or `PHYTOMNI_API_KEYS_DB` | `.cache/phytomni/api_keys.sqlite` (**CWD-relative**) | no         | API key SQLite store                              |
| `API_TASKS_DB_PATH` or `PHYTOMNI_TASKS_DB`   | `server_tasks.db` (**CWD-relative**)                 | no         | Runs / tasks SQLite store                         |
| `API_REQUEST_TIMEOUT`                        | `600.0`                                              | no         | Per-request timeout in seconds                    |
| `API_RATE_LIMIT_PER_MIN`                     | `120`                                                | no         | Per-key quota per minute                          |
| `API_RUN_TTL_OK_HOURS`                       | `24`                                                 | no         | Retention for successful runs                     |
| `API_RUN_TTL_FAIL_DAYS`                      | `7`                                                  | no         | Retention for failed runs                         |
| `PHYTOMNI_LICENSE_KEY`                       | — (no default)                                       | **yes**    | License for decrypting `.env.encrypted`           |
| `PHYTOMNI_TESTING`                           | unset                                                | no         | Set to `1` to inject dummy secrets (testing only) |

Important caveats:

- Default paths are **relative to the current working directory**. Either
  pin `WorkingDirectory=` in the systemd unit, or pass absolute paths in
  the environment file
- `PHYTOMNI_LICENSE_KEY` must not be checked into version control or
  baked into a docker image layer; inject it via `--env-file`,
  `EnvironmentFile=`, or a secret manager
- `PHYTOMNI_TESTING=1` neutralizes authentication; **never** enable it
  in production

## 9. Logging and observability

### 9.1 Defaults

`phytomni-api` does not set `log_level` or `log_config`; uvicorn defaults
apply:

- Level: `INFO`
- Sinks: stdout / stderr
- Format: uvicorn's standard one-line-per-request access log

To change the level set `LOG_LEVEL=warning` (uvicorn picks it up) or
launch the service through a custom wrapper that calls `dictConfig`.
No CLI flag is exposed today.

### 9.2 `X-Request-Id`

Every HTTP response carries `X-Request-Id` in the form
`request-<timestamp>-<short>`. The ASGI middleware generates a fresh id
per request; the current version does not honor a client-supplied
`X-Request-Id`.

`error.request_id` in the error envelope is the same value as the
response header, making customer-reported ids directly searchable in
the server log.

### 9.3 Recommended log collection

For systemd deployments: `journalctl -u phytomni-api -f` plus
vector / fluent-bit / similar to ship logs to a central aggregator.

For docker deployments: `docker logs <container>` with the standard
log driver (`json-file`, `journald`, or a custom collector).

The service writes no file-based logs by default — no extra log
volumes are required.

## 10. Triage SOPs

### 10.1 "API returns 401"

Likely causes, in descending order of probability:

1. The customer's stored key is typo'd or was truncated during copy —
   ask them to run `phytomni-api-key list --user-id <their user>` and
   compare the `PREFIX` (first 12 characters) against the key they hold
1. The key was revoked — `list` shows `ACTIVE=False`
1. The key expired — it was created with `--expires-days`
1. The service and the CLI are pointing at **different**
   `API_KEYS_DB_PATH` values — verify both have the same environment

Do not assume a PBKDF2 hash mismatch; the comparison is deterministic.
Nearly every 401 is one of the four cases above.

### 10.2 "API returns 429"

Read the response header `Retry-After`; tell the customer to back off
that many seconds.

If the customer is consistently throttled, decide based on intent:

- Single key insufficient → raise `API_RATE_LIMIT_PER_MIN` and restart
  the service
- Genuine burst traffic → issue additional keys for the same `user_id`
  and let the client rotate among them

Note that the limiter is single-process: a restart clears the counter
(make sure the customer is aware so they do not misread it as "limit
not enforced").

### 10.3 "My run is stuck"

Get the customer's `run_id` (the `id` field from the 202 response). Then:

```bash
curl -fsS -H "Authorization: Bearer $KEY" "$HOST/v1/runs/$RUN_ID"
```

Read the response:

- `status == "running"` — still in flight; quote an expected duration
  (Analyst, DeepGenome, Research, etc. routinely take 30 minutes to
  3 hours)
- `status == "failed"` — read the `error` field, escalate to application
  engineers
- `status == "succeeded"` but the customer claims they never received it
  — they may have started polling after the run expired (TTL defaults
  to 24 hours for successful runs)

If the customer only has a `task_id` and not a `run_id`, there is no
public endpoint that reverse-maps `task_id` to `run_id`. Operations can
query SQLite directly:

```bash
sqlite3 "$API_TASKS_DB_PATH" \
  "SELECT run_id, agent, status FROM tasks WHERE task_id = '$TASK_ID';"
```

### 10.4 "Analyst returned `id: null` — is that a bug?"

**Not a bug, expected behavior.** Analyst has a submission fingerprint
deduplication mechanism:

- The customer submits the same `(goal_description, data_list, obs_file_list)` again

- The service recognizes the fingerprint as a hit against a prior task
  and **deliberately** skips creating a new run so the original
  `run_id` remains authoritative

- Response shape:

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

- The customer should poll the original task using `result.task_id`
  directly, not `GET /v1/runs/null`

How to tell this is dedup rather than a failure: `result.dedup_hit == true` is present. When `id` is `null` **without** that marker, the
internal recorder failed silently and the request should be retried.

### 10.5 Service startup failures

Inspect `journalctl -u phytomni-api` and match the exception:

| Error                                                      | Meaning                                             | Fix                                                                       |
| ---------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------- |
| `SecretEnvelopeError: wrong license key or corrupted file` | License key is wrong or `.env.encrypted` is damaged | Verify `PHYTOMNI_LICENSE_KEY`, or redeliver `.env.encrypted`              |
| `RuntimeError: cannot resolve environment`                 | No plaintext `.env`, no envelope, no testing flag   | Provide one of those three                                                |
| `PermissionError` on a SQLite path                         | The DB directory is not writable                    | Fix permissions or repoint `API_KEYS_DB_PATH`                             |
| `OSError: [Errno 98]` (address already in use)             | Port 8080 is already bound                          | Find the holder with `lsof -i :8080`, free the port, or change `API_PORT` |

If the service starts but every authenticated endpoint returns 500,
check `/readyz` first:

- 503 → the SQLite directories checked by `/readyz` are not writable;
  fix the directory permissions
- 200 but endpoints still 500 → inspect the stderr stack trace, escalate
  to application engineers

## 11. Disaster recovery and routine operations

### 11.1 Backups

Daily:

```bash
# Use SQLite's .backup command for a transaction-safe hot copy
sqlite3 "$API_KEYS_DB_PATH" ".backup /backup/$(date +%F)/api_keys.sqlite"
sqlite3 "$API_TASKS_DB_PATH" ".backup /backup/$(date +%F)/server_tasks.db"
```

To restore: stop the service, copy the backup file into place (let
SQLite rebuild the WAL and SHM files on next open), then start the
service.

Do not `cp` a WAL-mode SQLite file directly while the service is
running — you may capture an unflushed transaction.

### 11.2 Restart

```bash
systemctl restart phytomni-api
journalctl -u phytomni-api -n 50 -f
```

A restart clears:

- The rate-limit counter (the same happens with multiple workers)
- Any non-persistent function cache state held in memory (persistent
  on-disk cache rows survive)

A restart does **not** clear the API key store or the runs DB.

### 11.3 Upgrade flow

Zero-downtime is not possible with a single process, but a fast
swap is:

1. Drain readiness: lower `/readyz` weight on the load balancer to 0 so
   the upstream removes this node
1. `systemctl stop phytomni-api`
1. Upgrade the code: `uv pip install -e /path/to/new/Phytomni-Bot`
1. `systemctl start phytomni-api`
1. Run the smoke script from section 4.4
1. Once all checks pass, raise the LB weight back up

For multi-host deployments, roll one host at a time.

## Appendix: related source and documentation

- Application-engineer-facing client SDK and MCP tool semantics: see
  `README.md`
- Agent internals (not required for operations): see `CLAUDE.md` and
  `AGENTS.md`
- Configuration defaults: `src/mcp_server_phytomni/config/defaults.py`
  (`ApiConfig`)
- API key hashing and authentication: `src/mcp_server_phytomni/api/auth.py`
- Rate limiter: `src/mcp_server_phytomni/api/ratelimit.py`
- Full endpoint set: `src/mcp_server_phytomni/api/app.py`
