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
1. `src/mcp_server_phytomni/config/.env` — plaintext, local dev.
1. `src/mcp_server_phytomni/config/.env.encrypted` plus
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

## Deployment Endpoints and UUIDs

Nineteen per-deployment endpoints and repository identifiers that
used to live as hardcoded defaults in `config/defaults.py` are now
required-via-env so a customer image never ships with another
customer's IPs, UUIDs, regional cloud-platform hosts, or workspace
ids baked in. Each accepts an unprefixed or `PHYTOMNI_`-prefixed
alias and validates non-empty at startup; a missing or empty value
raises a `ValidationError` naming the field so an operator sees the
env-var label they need to set, rather than a downstream `404` /
`KeyError` at first agent call.

| Variable           | Aliased as                  | Purpose                                                                                                                 |
| ------------------ | --------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `TOKEN_URL`        | `PHYTOMNI_TOKEN_URL`        | IAM token-acquisition endpoint (legacy default: `iam.cn-southwest-2.myhuaweicloud.com/v3/auth/tokens`).                 |
| `RETRIEVE_URL`     | `PHYTOMNI_RETRIEVE_URL`     | Document-retrieval endpoint used by KnowledgeAgent / DataAgent / AnalystAgent.                                          |
| `RERANK_URL`       | `PHYTOMNI_RERANK_URL`       | Document-reranking endpoint used downstream of `RETRIEVE_URL`.                                                          |
| `CREATE_TASK_URL`  | `PHYTOMNI_CREATE_TASK_URL`  | DeepGenome remote task-creation endpoint.                                                                               |
| `UPDATE_TASK_URL`  | `PHYTOMNI_UPDATE_TASK_URL`  | DeepGenome remote task-status update endpoint.                                                                          |
| `SPA_FAQ_URL`      | `PHYTOMNI_SPA_FAQ_URL`      | SPA-faq lookup template; expects `{repo_id}` substitution.                                                              |
| `DATABASE_URL`     | `PHYTOMNI_DATABASE_URL`     | NL-query database endpoint; the legacy default embedded the workspace UUID directly inside the URL path.                |
| `ANALYSIS_URL`     | `PHYTOMNI_ANALYSIS_URL`     | EI-Health workflow endpoint; the legacy default embedded project and job UUIDs directly inside the URL path.            |
| `BI_URL`           | `PHYTOMNI_BI_URL`           | BI gene-annotation lookup endpoint used by BriefGeneAgent and DeepGenomeAgent (legacy default: `phytomni.cn/api/data`). |
| `OBS_SERVER`       | `PHYTOMNI_OBS_SERVER`       | OBS regional host (legacy default: `obs.cn-east-3.myhuaweicloud.com`).                                                  |
| `REPO_ID`          | `PHYTOMNI_REPO_ID`          | Primary knowledge-repo UUID.                                                                                            |
| `REPO_ID_DICT`     | `PHYTOMNI_REPO_ID_DICT`     | JSON-string `{ "<repo_uuid>": <token_budget>, ... }`; parsed into a `Dict[str, int]` by pydantic-settings.              |
| `WORKSPACE_ID`     | `PHYTOMNI_WORKSPACE_ID`     | Workspace UUID used by NL-query and analysis paths.                                                                     |
| `SUBJECT_ID`       | `PHYTOMNI_SUBJECT_ID`       | NL-query database subject / schema UUID.                                                                                |
| `DATA_REPO_ID`     | `PHYTOMNI_DATA_REPO_ID`     | DataAgent retrieval repo UUID.                                                                                          |
| `TOOL_REPO_ID`     | `PHYTOMNI_TOOL_REPO_ID`     | Analyst tool-retrieval repo UUID.                                                                                       |
| `PROTOCOL_REPO_ID` | `PHYTOMNI_PROTOCOL_REPO_ID` | DeepGenome protocol-retrieval repo UUID.                                                                                |
| `SPA_REPO_ID`      | `PHYTOMNI_SPA_REPO_ID`      | DeepGenome SPA-repo UUID feeding into `SPA_FAQ_URL`.                                                                    |
| `APP_ID`           | `PHYTOMNI_APP_ID`           | JSON-string `{ "small": "<uuid>", "medium": "<uuid>", "large": "<uuid>" }`; analyst compute-tier app-id map.            |

The aliasing matches the existing `PHYTOMNI_TLS_VERIFY` / `PHYTOMNI_CA_BUNDLE` convention so deployments may use the prefixed form when other `PHYTOMNI_*` variables already dominate the runtime environment. The two `Dict`-valued entries (`REPO_ID_DICT` and `APP_ID`) ship as JSON strings (e.g. `PHYTOMNI_REPO_ID_DICT='{"a34b...77b":128,"ec3...b":64}'`, `PHYTOMNI_APP_ID='{"small":"<uuid>","medium":"<uuid>","large":"<uuid>"}'`) so a single env var carries the full map.

## HTTP API Variables

| Variable                     | Default                           | Sensitive? | Purpose                                                                                                             |
| ---------------------------- | --------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------- |
| `API_HOST`                   | `127.0.0.1`                       | no         | Uvicorn bind address.                                                                                               |
| `API_PORT`                   | `8080`                            | no         | Uvicorn bind port.                                                                                                  |
| `API_KEYS_DB_PATH`           | `.cache/phytomni/api_keys.sqlite` | no         | Per-user API key SQLite store.                                                                                      |
| `PHYTOMNI_API_KEYS_DB`       | unset                             | no         | Backward-compatible API key store alias.                                                                            |
| `API_TASKS_DB_PATH`          | `server_tasks.db`                 | no         | Runs and tasks SQLite store.                                                                                        |
| `PHYTOMNI_TASKS_DB`          | unset                             | no         | Backward-compatible runs/tasks store alias.                                                                         |
| `API_SERVICE_TOKEN`          | unset                             | yes        | Service-to-service token gating `/v1/api-keys` admin routes; unset disables them with `503 admin path not enabled`. |
| `PHYTOMNI_API_SERVICE_TOKEN` | unset                             | yes        | Backward-compatible service token alias.                                                                            |
| `API_REQUEST_TIMEOUT`        | `600.0`                           | no         | Per-request timeout in seconds.                                                                                     |
| `API_RATE_LIMIT_PER_MIN`     | `120`                             | no         | Per-key request budget per minute; `<= 0` disables.                                                                 |
| `API_RUN_TTL_OK_HOURS`       | `24`                              | no         | Retention for succeeded runs.                                                                                       |
| `API_RUN_TTL_FAIL_DAYS`      | `7`                               | no         | Retention for failed runs.                                                                                          |

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

## Agent Composition Variables

| Variable                                                 | Default | Purpose                                                                                                                                                                                                                                                                                                                                |
| -------------------------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PHYTOMNI_USE_ANALYST_SUBGRAPH` / `USE_ANALYST_SUBGRAPH` | `false` | Per-deployment opt-in for dispatchers that submit work through `AnalystAgent`. When `true`, the design / network / research / environment / deep_genome dispatchers invoke the analyst via its compiled subgraph entry point (`analyst_agent.app.ainvoke(AnalystInput, …)`) instead of the legacy `analyst_agent.arun(…)` direct call. |

The flag lives on `AnalystConfig` and is therefore inherited by every
dispatcher subclass (`DigitalDesignConfig`, `GeneNetworkConfig`,
`InSilicoResearchConfig`, `EnvironmentConfig`, `DeepGenomeConfig`). Both
dispatch paths share the same `RunIdentity`, OBS output directory, and
LangGraph `thread_id` because both call
`prepare_analyst_dispatch_context` before invoking the analyst, so the
flag only changes the analyst entry point — not the IO contract the
downstream `capture_analysis_result` consumer reads. Production
deployments should leave the flag `false` until they have validated the
subgraph composition end to end; the legacy direct-`arun` path remains
the default and is unaffected when the flag is unset.

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
