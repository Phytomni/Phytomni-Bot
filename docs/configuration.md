# Configuration

Phytomni-Bot resolves configuration from environment variables, local
developer `.env` files, or encrypted customer envelopes. Real secrets must
never be committed, printed in logs, or baked into a public image layer.

## Local Developer `.env`

Copy the example file and fill in real credentials:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

Common variables:

| Variable            | Required | Purpose                                 |
| ------------------- | -------- | --------------------------------------- |
| `DOMAIN_NAME`       | yes      | Huawei IAM domain name.                 |
| `USER_NAME`         | yes      | Huawei IAM user name.                   |
| `USER_PASSWORD`     | yes      | Huawei IAM password.                    |
| `ACCESS_KEY_ID`     | yes      | OBS access key id.                      |
| `SECRET_ACCESS_KEY` | yes      | OBS secret access key.                  |
| `BASE_URL`          | yes      | Primary LLM base URL.                   |
| `MODEL_ID`          | yes      | Primary LLM model id.                   |
| `API_KEY`           | yes      | Primary outbound LLM API key.           |
| `CODER_URL`         | yes      | Coder model base URL.                   |
| `CODER_MODEL`       | yes      | Coder model id.                         |
| `CODER_API_KEY`     | yes      | Coder model API key.                    |
| `EMBED_URL`         | yes      | Embedding service base URL.             |
| `EMBED_MODEL`       | yes      | Embedding model id.                     |
| `EMBED_API_KEY`     | yes      | Embedding service API key.              |
| `BI_TOKEN`          | no       | DeepGenome BI token; defaults to empty. |

Legacy `AccessKeyID` and `SecretAccessKey` aliases are still accepted for
compatibility. New local configuration should use `ACCESS_KEY_ID` and
`SECRET_ACCESS_KEY`.

## Resolution order

At startup `load_env_file()` tries sources in this order:

1. `PHYTOMNI_TESTING=1` — test suites only.
2. `src/mcp_server_phytomni/config/.env` — plaintext, local dev.
3. `src/mcp_server_phytomni/config/.env.encrypted` plus
   `PHYTOMNI_LICENSE_KEY` (env var) or `config/.license_key` (file) —
   customer-image fallback.

Customer images never contain a plaintext `.env` (blocked by
`.dockerignore`), so they fall straight through to step 3 regardless
of the new precedence.

## Encrypted Customer Envelope

Trusted-customer images contain `.env.encrypted`, not plaintext
`.env`. Operators create the envelope with:

```bash
python scripts/encrypt_env.py \
  --input src/mcp_server_phytomni/config/.env \
  --license-key "<per-customer-license-key>" \
  --output src/mcp_server_phytomni/config/.env.encrypted
```

At runtime the customer supplies the license key by either:

- `PHYTOMNI_LICENSE_KEY`
- `src/mcp_server_phytomni/config/.license_key`

The environment variable wins when both are present. The decrypted values
are loaded into process environment only and are never written back to
disk.

## HTTP API Variables

| Variable                 | Default                           | Sensitive? | Purpose                                             |
| ------------------------ | --------------------------------- | ---------- | --------------------------------------------------- |
| `API_HOST`               | `127.0.0.1`                       | no         | Uvicorn bind address.                               |
| `API_PORT`               | `8080`                            | no         | Uvicorn bind port.                                  |
| `API_KEYS_DB_PATH`       | `.cache/phytomni/api_keys.sqlite` | no         | Per-user API key SQLite store.                      |
| `PHYTOMNI_API_KEYS_DB`   | unset                             | no         | Backward-compatible API key store alias.            |
| `API_TASKS_DB_PATH`      | `server_tasks.db`                 | no         | Runs and tasks SQLite store.                        |
| `PHYTOMNI_TASKS_DB`      | unset                             | no         | Backward-compatible runs/tasks store alias.         |
| `API_REQUEST_TIMEOUT`    | `600.0`                           | no         | Per-request timeout in seconds.                     |
| `API_RATE_LIMIT_PER_MIN` | `120`                             | no         | Per-key request budget per minute; `<= 0` disables. |
| `API_RUN_TTL_OK_HOURS`   | `24`                              | no         | Retention for succeeded runs.                       |
| `API_RUN_TTL_FAIL_DAYS`  | `7`                               | no         | Retention for failed runs.                          |

SQLite store defaults are relative to the service working directory. In
systemd or container deployments, set absolute paths or pin the service
working directory so restarts use the same stores.

## Cache and Registry Variables

| Variable            | Default                             | Purpose                                                  |
| ------------------- | ----------------------------------- | -------------------------------------------------------- |
| `PHYTOMNI_CACHE_DB` | `.cache/phytomni/func_cache.sqlite` | Function cache SQLite path.                              |
| `PHYTOMNI_TESTING`  | unset                               | Set to `1` only in tests to disable real `.env` loading. |

Function caches stay on local disk even when obsfs is available because
SQLite over a network filesystem can deadlock under WAL locking.

## Network and TLS Variables

| Variable              | Default | Sensitive? | Purpose                                                                                               |
| --------------------- | ------- | ---------- | ----------------------------------------------------------------------------------------------------- |
| `PHYTOMNI_TLS_VERIFY` | `true`  | no         | Disable peer certificate verification when set to `0`/`false`/`no` (dev or pinned on-prem only).      |
| `PHYTOMNI_CA_BUNDLE`  | unset   | no         | Absolute path to a PEM CA bundle. Honoured when verification is on; ignored when verification is off. |

Every async HTTP call in `mcp_server_phytomni` should flow through
`common.httpx_client.get_async_client`, which reads these settings once
per call and hands the resolved `verify` argument to `httpx.AsyncClient`.
A missing or unreadable `PHYTOMNI_CA_BUNDLE` path surfaces as the same
`ssl.SSLError` the underlying SDK would emit; flip
`PHYTOMNI_TLS_VERIFY=0` in dev environments behind a corporate proxy or
self-signed cluster ingress only — production deployments should ship a
real CA bundle instead.

## Live E2E Variables

| Variable                                | Default           | Purpose                                                       |
| --------------------------------------- | ----------------- | ------------------------------------------------------------- |
| `PHYTOMNI_RUN_INTEGRATION`              | unset             | Set to `1` to allow integration tests.                        |
| `PHYTOMNI_ALLOW_NETWORK`                | unset             | Set to `1` to allow network tests.                            |
| `PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS`   | `1800`            | Submit timeout for async tool calls.                          |
| `PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS`     | `600`             | Polling deadline for one async task.                          |
| `PHYTOMNI_E2E_TASKS_DB`                 | `server_tasks.db` | Override the task registry used by e2e polling.               |
| `PHYTOMNI_E2E_RUN_KA_UPLOAD`            | unset             | Set to `1` to include the slow KnowledgeAgent upload variant. |
| `PHYTOMNI_E2E_API_STARTUP_SECONDS`      | `120`             | HTTP API e2e startup health-gate budget.                      |
| `PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS` | `1200`            | HTTP API e2e per-request read timeout.                        |

## Do Not Commit

- Plaintext `.env`
- `.env.encrypted` generated for a real customer unless explicitly intended
  for that distribution workflow
- `.license_key`
- API keys, OBS credentials, model keys, or copied customer tokens
- Local SQLite registries, cache databases, WAL/SHM files, and virtual
  environments
