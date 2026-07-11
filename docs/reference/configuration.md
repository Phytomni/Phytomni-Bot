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

| Variable            | Required | Purpose                                                                                                                                                 |
| ------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DOMAIN_NAME`       | yes      | Huawei IAM domain name.                                                                                                                                 |
| `USER_NAME`         | yes      | Huawei IAM user name.                                                                                                                                   |
| `USER_PASSWORD`     | yes      | Huawei IAM password.                                                                                                                                    |
| `ACCESS_KEY_ID`     | yes      | OBS access key id.                                                                                                                                      |
| `SECRET_ACCESS_KEY` | yes      | OBS secret access key.                                                                                                                                  |
| `BASE_URL`          | yes      | Primary LLM base URL.                                                                                                                                   |
| `MODEL_ID`          | yes      | Primary LLM model id.                                                                                                                                   |
| `API_KEY`           | yes      | Primary outbound LLM API key.                                                                                                                           |
| `CODER_URL`         | yes      | Coder model base URL.                                                                                                                                   |
| `CODER_MODEL`       | yes      | Coder model id.                                                                                                                                         |
| `CODER_API_KEY`     | yes      | Coder model API key.                                                                                                                                    |
| `EMBED_URL`         | yes      | Embedding service base URL.                                                                                                                             |
| `EMBED_MODEL`       | yes      | Embedding model id.                                                                                                                                     |
| `EMBED_API_KEY`     | yes      | Embedding service API key.                                                                                                                              |
| `GAUSS_DSN`         | yes      | Direct GaussDB DSN for the BI query path. Required outside relay mode; sealed in the encrypted envelope. URL-encode special characters in the password. |

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

The input `.env` must be valid UTF-8 **without** a byte-order mark
(BOM). `encrypt_env.py` rejects GBK / ANSI / BOM-encoded input with a
clear error and exit code `4`, so a mis-encoded source file cannot be
sealed into an image — where it would otherwise surface as a cryptic
`UnicodeDecodeError` at customer startup.

At runtime the customer supplies the license key by either:

- `PHYTOMNI_LICENSE_KEY`
- `src/mcp_server_phytomni/config/.license_key`

The environment variable wins when both are present. The decrypted values
are loaded into process environment only and are never written back to
disk.

## Deployment Endpoints and UUIDs

Sixteen per-deployment endpoints and repository identifiers that
used to live as hardcoded defaults in `config/defaults.py` are now
required-via-env so a customer image never ships with another
customer's IPs, UUIDs, regional cloud-platform hosts, or workspace
ids baked in. Each accepts an unprefixed or `PHYTOMNI_`-prefixed
alias and validates non-empty at startup; a missing or empty value
raises a `ValidationError` naming the field so an operator sees the
env-var label they need to set, rather than a downstream `404` /
`KeyError` at first agent call.

This fail-fast non-empty validation is normal-mode only. In customer
relay mode (`PHYTOMNI_RELAY_MODE=1`) the `_require_non_empty_endpoint`
validator short-circuits, so a relay-mode child Bot boots with all 16
operator endpoints empty; it instead routes every dependency through the
upstream relay via `RELAY_BASE_URL` / `RELAY_API_KEY`, and roots its OBS
object paths under the operator-assigned tenant id `RELAY_USER_ID` (which
must match the user id the operator bound to the relay key, since the
operator's OBS relay confines each key to its own tenant namespace). See
`config/.env.customer.example` for the minimal child variable set.

| Variable           | Aliased as                  | Purpose                                                                                                      |
| ------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `TOKEN_URL`        | `PHYTOMNI_TOKEN_URL`        | IAM token-acquisition endpoint (legacy default: `iam.cn-southwest-2.myhuaweicloud.com/v3/auth/tokens`).      |
| `RETRIEVE_URL`     | `PHYTOMNI_RETRIEVE_URL`     | Document-retrieval endpoint used by KnowledgeAgent / DataAgent / AnalystAgent.                               |
| `RERANK_URL`       | `PHYTOMNI_RERANK_URL`       | Document-reranking endpoint used downstream of `RETRIEVE_URL`.                                               |
| `SPA_FAQ_URL`      | `PHYTOMNI_SPA_FAQ_URL`      | SPA-faq lookup template; expects `{repo_id}` substitution.                                                   |
| `DATABASE_URL`     | `PHYTOMNI_DATABASE_URL`     | NL-query database endpoint; the legacy default embedded the workspace UUID directly inside the URL path.     |
| `ANALYSIS_URL`     | `PHYTOMNI_ANALYSIS_URL`     | EI-Health workflow endpoint; the legacy default embedded project and job UUIDs directly inside the URL path. |
| `OBS_SERVER`       | `PHYTOMNI_OBS_SERVER`       | OBS regional host (legacy default: `obs.cn-east-3.myhuaweicloud.com`).                                       |
| `REPO_ID`          | `PHYTOMNI_REPO_ID`          | Primary knowledge-repo UUID.                                                                                 |
| `REPO_ID_DICT`     | `PHYTOMNI_REPO_ID_DICT`     | JSON-string `{ "<repo_uuid>": <token_budget>, ... }`; parsed into a `Dict[str, int]` by pydantic-settings.   |
| `WORKSPACE_ID`     | `PHYTOMNI_WORKSPACE_ID`     | Workspace UUID used by NL-query and analysis paths.                                                          |
| `SUBJECT_ID`       | `PHYTOMNI_SUBJECT_ID`       | NL-query database subject / schema UUID.                                                                     |
| `DATA_REPO_ID`     | `PHYTOMNI_DATA_REPO_ID`     | DataAgent retrieval repo UUID.                                                                               |
| `TOOL_REPO_ID`     | `PHYTOMNI_TOOL_REPO_ID`     | Analyst tool-retrieval repo UUID.                                                                            |
| `PROTOCOL_REPO_ID` | `PHYTOMNI_PROTOCOL_REPO_ID` | DeepGenome protocol-retrieval repo UUID.                                                                     |
| `SPA_REPO_ID`      | `PHYTOMNI_SPA_REPO_ID`      | DeepGenome SPA-repo UUID feeding into `SPA_FAQ_URL`.                                                         |
| `APP_ID`           | `PHYTOMNI_APP_ID`           | JSON-string `{ "small": "<uuid>", "medium": "<uuid>", "large": "<uuid>" }`; analyst compute-tier app-id map. |

The aliasing matches the existing `PHYTOMNI_TLS_VERIFY` / `PHYTOMNI_CA_BUNDLE` convention so deployments may use the prefixed form when other `PHYTOMNI_*` variables already dominate the runtime environment. The two `Dict`-valued entries (`REPO_ID_DICT` and `APP_ID`) ship as JSON strings (e.g. `PHYTOMNI_REPO_ID_DICT='{"a34b...77b":128,"ec3...b":64}'`, `PHYTOMNI_APP_ID='{"small":"<uuid>","medium":"<uuid>","large":"<uuid>"}'`) so a single env var carries the full map.

## HTTP API Variables

| Variable                     | Default                           | Sensitive? | Purpose                                                                                                                                                                             |
| ---------------------------- | --------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `API_HOST`                   | `127.0.0.1`                       | no         | Uvicorn bind address.                                                                                                                                                               |
| `API_PORT`                   | `8080`                            | no         | Uvicorn bind port.                                                                                                                                                                  |
| `API_GRACEFUL_SHUTDOWN`      | `30`                              | no         | Uvicorn graceful-shutdown drain window in seconds; kept shorter than systemd's `TimeoutStopSec`.                                                                                    |
| `API_KEYS_DB_PATH`           | `.cache/phytomni/api_keys.sqlite` | no         | Per-user API key SQLite store.                                                                                                                                                      |
| `PHYTOMNI_API_KEYS_DB`       | unset                             | no         | Backward-compatible API key store alias.                                                                                                                                            |
| `API_TASKS_DB_PATH`          | `server_tasks.db`                 | no         | Runs and tasks SQLite store.                                                                                                                                                        |
| `PHYTOMNI_TASKS_DB`          | unset                             | no         | Backward-compatible runs/tasks store alias.                                                                                                                                         |
| `API_SERVICE_TOKEN`          | unset                             | yes        | Service-to-service token gating `/v1/api-keys` admin routes; unset disables them with `503 admin path not enabled`.                                                                 |
| `PHYTOMNI_API_SERVICE_TOKEN` | unset                             | yes        | Backward-compatible service token alias.                                                                                                                                            |
| `API_UPLOAD_MAX_BYTES`       | `26214400`                        | no         | Per-file ceiling for `POST /v1/files`, in bytes (25 MiB); oversize uploads return `413`.                                                                                            |
| `API_UPLOAD_PREFIX`          | `agent_data/uploads`              | no         | OBS object-key prefix below the bucket root for `POST /v1/files` upload outputs.                                                                                                    |
| `API_REQUEST_TIMEOUT`        | `600.0`                           | no         | Per-request timeout in seconds.                                                                                                                                                     |
| `API_RATE_LIMIT_PER_MIN`     | `120`                             | no         | Per-key request budget per minute; `<= 0` disables.                                                                                                                                 |
| `API_RUN_TTL_OK_HOURS`       | `24`                              | no         | Retention for succeeded runs.                                                                                                                                                       |
| `API_RUN_TTL_FAIL_DAYS`      | `7`                               | no         | Retention for failed runs.                                                                                                                                                          |
| `STREAM_ANSWER_MAX_BYTES`    | `1048576`                         | no         | Soft UTF-8 byte cap for ChatAgent streamed-answer persistence in the run registry (1 MiB); the live SSE wire stream is never truncated. Accepts `PHYTOMNI_STREAM_ANSWER_MAX_BYTES`. |
| `A2UI_ENABLED`               | `false`                           | no         | When true, ChatAgent streamed chat may emit A2UI confirm surfaces and accept actions on `POST /v1/runs/{run_id}/a2ui-actions`. Accepts `PHYTOMNI_A2UI_ENABLED`.                     |
| `A2UI_TOOL_CALL`             | `false`                           | no         | Reserved for future A2UI tool-call emit on the chat path; unused in the default P4-1 confirm slice. Accepts `PHYTOMNI_A2UI_TOOL_CALL`.                                              |
| `A2A_ENABLED`                | `false`                           | no         | Feature flag for the A2A v1 JSON-RPC surface; disabled by default and requires `A2A_PUBLIC_BASE_URL` when enabled. Accepts `PHYTOMNI_A2A_ENABLED`.                                  |
| `A2A_PUBLIC_BASE_URL`        | `unset`                           | no         | Absolute HTTP(S) public URL prefix used to build the A2A Agent Card and `/a2a` interface; trailing slashes are removed. Accepts `PHYTOMNI_A2A_PUBLIC_BASE_URL`.                     |

SQLite store defaults are relative to the service working directory. In
systemd or container deployments, set absolute paths or pin the service
working directory so restarts use the same stores.

When A2A is enabled, `/.well-known/agent-card.json` is public and `/a2a`
requires an API key with the `agents` scope plus `A2A-Version: 1.0`. Phase 1
advertises only the non-streaming `SendMessage` method; the flag remains off
by default so existing deployments keep their previous route surface.

## Relay Variables

These tune the credential-injecting relay (`/v1/relay/*`). The relay
reuses the existing upstream endpoints (see *Deployment Endpoints and
UUIDs*) and operator secrets (see *Encrypted Customer Envelope*) — there
is no relay-specific secret. Every variable accepts an unprefixed or
`PHYTOMNI_RELAY_*` alias.

| Variable                         | Default                              | Sensitive? | Purpose                                                                                                         |
| -------------------------------- | ------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------------- |
| `RELAY_ENABLED`                  | `false`                              | no         | Expose `/v1/relay/*`; re-read every request so a disable is an instant kill-switch.                             |
| `RELAY_AUDIT_DB_PATH`            | `.cache/phytomni/relay_audit.sqlite` | no         | Local relay audit SQLite store; keep on a local disk (WAL deadlocks on network filesystems).                    |
| `RELAY_AUDIT_RETENTION_DAYS`     | `90`                                 | no         | Age in days after which audit rows are eligible for cleanup.                                                    |
| `RELAY_REQUEST_MAX_BYTES`        | `10485760`                           | no         | Max relayed request body in bytes; over-limit returns `413` without buffering the whole body.                   |
| `RELAY_RESPONSE_AUDIT_MAX_BYTES` | `10485760`                           | no         | Max upstream response bytes copied into the audit; the client-facing response is never truncated.               |
| `RELAY_RESPONSE_MAX_BYTES`       | `1073741824`                         | no         | Max OBS object size the download relay streams back before returning `413`; distinct from the request-body cap. |
| `RELAY_TIMEOUT_SECONDS`          | `600.0`                              | no         | Per-request upstream timeout and the total wall-clock ceiling for a streamed forward.                           |
| `RELAY_RATE_LIMIT_PER_MIN`       | `60`                                 | no         | Per-key relay request budget per minute (separate from `API_RATE_LIMIT_PER_MIN`); over-limit `429`.             |
| `RELAY_MAX_CONCURRENT_PER_KEY`   | `8`                                  | no         | Max in-flight relay forwards per key; excess returns `503`.                                                     |

Rate, concurrency, and retention state are per worker, so the effective
per-key ceilings scale with the worker count. See
`docs/ops/http-api-runbook.md` *Relay Operations* for the operator
procedures and `docs/reference/http-api.md` *Relay* for the route contracts.

## Cache and Registry Variables

| Variable            | Default                             | Purpose                                                  |
| ------------------- | ----------------------------------- | -------------------------------------------------------- |
| `PHYTOMNI_CACHE_DB` | `.cache/phytomni/func_cache.sqlite` | Function cache SQLite path.                              |
| `PHYTOMNI_TESTING`  | unset                               | Set to `1` only in tests to disable real `.env` loading. |

Function caches stay on local disk even when obsfs is available because
SQLite over a network filesystem can deadlock under WAL locking.

## Agent Composition

Consumer agents compose their building-block subgraphs unconditionally —
there are no opt-in flags. Chat calls route through the compiled chat
subgraph, retrieval through the `KnowledgeAgent` compiled subgraph, and
dispatcher analyst submissions through the analyst subgraph entry point
(`analyst_agent.app.ainvoke(AnalystInput, …)`); the dispatcher calls
`prepare_analyst_dispatch_context` first so the analyst and the
downstream `capture_analysis_result` consumer share the same
`RunIdentity`, OBS output directory, and LangGraph `thread_id`. The
deep_genome dispatcher routes its `protein_structure_analysis` and
`promoter_analysis` tasks to the module-level producer wrappers
(`protein_structure_for_gene` / `promoter_design_for_gene`), while
`evolution_analysis` and `digital_design` mount the standalone evolution
and DigitalDesign graphs as structural subgraphs (`evolution_node` /
`design_node`) rather than routing through a producer wrapper.

The former `USE_CHAT_SUBGRAPH` / `USE_KNOWLEDGE_SUBGRAPH` /
`USE_ANALYST_SUBGRAPH` / `USE_EVOLUTION_SUBGRAPH` / `USE_DESIGN_SUBGRAPH`
opt-in flags and their legacy flag-off code paths have been removed; the
subgraph composition is now the only path and no env override exists for
it.

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

## Server Tuning Variables

| Variable                | Default | Sensitive? | Purpose                                                                                       |
| ----------------------- | ------- | ---------- | --------------------------------------------------------------------------------------------- |
| `GAUSS_COMMAND_TIMEOUT` | `30.0`  | no         | Per-query timeout in seconds for the direct GaussDB pool (`agents/shared/gauss.py`).          |
| `HTTP_MAX_CONNECTIONS`  | `100`   | no         | Max total connections for the shared `httpx.AsyncClient` pool (`common/httpx_client.py`).     |
| `HTTP_MAX_KEEPALIVE`    | `50`    | no         | Max keepalive connections for the shared `httpx.AsyncClient` pool (`common/httpx_client.py`). |

These are `ServerConfig` fields read once at startup. `GAUSS_COMMAND_TIMEOUT`
bounds each direct GaussDB query so a stuck backend cannot hold a pooled
connection indefinitely; `HTTP_MAX_CONNECTIONS` / `HTTP_MAX_KEEPALIVE` set
the `httpx.Limits` handed to the shared `AsyncClient` so heavy
`multi_retrieve` x `rerank` x relay fan-out reuses connections instead of
churning TCP/TLS handshakes. Leave them unset to accept the defaults.

## Retrieval Tuning Variables

| Variable                      | Default | Sensitive? | Purpose                                                                                          |
| ----------------------------- | ------- | ---------- | ------------------------------------------------------------------------------------------------ |
| `PHYTOMNI_RERANK_CONCURRENCY` | `16`    | no         | Max concurrent rerank HTTP requests per process event loop. `0` or negative disables throttling. |

The throttle is process-internal: each running event loop holds its own
`asyncio.Semaphore`, shared across every rerank-capable agent
(KnowledgeAgent / ReviewAgent / BriefGeneAgent / DeepGenome) in that
process. It bounds the rerank fan-out a single `ReviewAgent` run produces
(research dimensions x repositories x rerank batches) so the rerank
backend stays in its zero-failure latency region. The MCP stdio process
and the HTTP API process each keep an independent semaphore; the default
of `16` is chosen so even both processes saturated (`2 x 16 = 32`) stays
under the backend's hard-failure knee. Accepts the unprefixed
`RERANK_CONCURRENCY` or the `PHYTOMNI_RERANK_CONCURRENCY` form.

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
