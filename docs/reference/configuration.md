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

- **Variable:** `DOMAIN_NAME`
  **Required:** yes
  **Purpose:** Huawei IAM domain name.

- **Variable:** `USER_NAME`
  **Required:** yes
  **Purpose:** Huawei IAM user name.

- **Variable:** `USER_PASSWORD`
  **Required:** yes
  **Purpose:** Huawei IAM password.

- **Variable:** `ACCESS_KEY_ID`
  **Required:** yes
  **Purpose:** OBS access key id.

- **Variable:** `SECRET_ACCESS_KEY`
  **Required:** yes
  **Purpose:** OBS secret access key.

- **Variable:** `BASE_URL`
  **Required:** yes
  **Purpose:** Primary LLM base URL.

- **Variable:** `MODEL_ID`
  **Required:** yes
  **Purpose:** Primary LLM model id.

- **Variable:** `API_KEY`
  **Required:** yes
  **Purpose:** Primary outbound LLM API key.

- **Variable:** `CODER_URL`
  **Required:** yes
  **Purpose:** Coder model base URL.

- **Variable:** `CODER_MODEL`
  **Required:** yes
  **Purpose:** Coder model id.

- **Variable:** `CODER_API_KEY`
  **Required:** yes
  **Purpose:** Coder model API key.

- **Variable:** `EMBED_URL`
  **Required:** yes
  **Purpose:** Embedding service base URL.

- **Variable:** `EMBED_MODEL`
  **Required:** yes
  **Purpose:** Embedding model id.

- **Variable:** `EMBED_API_KEY`
  **Required:** yes
  **Purpose:** Embedding service API key.

- **Variable:** `GAUSS_DSN`
  **Required:** yes
  **Purpose:** Direct GaussDB DSN for the BI query path. Required outside relay
  mode; sealed in the encrypted envelope. URL-encode special
  characters in the
  password.

`GAUSS_DSN` is the current direct BI path. No runtime flag re-enables the
removed BI HTTP client; a legacy deployment must install the prior binary and
restore the environment expected by that binary.

### Citation SQLite artifact

The cited-result metadata artifact is configured by either
`CITATION_DB_PATH` or `PHYTOMNI_CITATION_DB_PATH`. The path is optional while
the package is imported or `create_app()` is constructed, so auxiliary tools
and route/schema tests remain usable. It is required before MCP stdio or the
FastAPI HTTP lifespan starts successfully and must name a readable regular
file on a local filesystem.

There is no repository, wheel, or container-image default. Serving code never
creates the database. Network filesystems are unsupported because the artifact
is built offline, externally mounted, and opened with SQLite `mode=ro` plus
`PRAGMA query_only=ON`. Build and validate the artifact with the operator
commands in the [citation database runbook](../ops/citation-database-runbook.md);
runtime citation lookup has no GaussDB fallback. `GAUSS_DSN` and relay BI
remain configuration for unrelated database paths.

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

- **Variable:** `TOKEN_URL`
  **Aliased as:** `PHYTOMNI_TOKEN_URL`
  **Purpose:** IAM token-acquisition endpoint (legacy default:
  `iam.cn-southwest-2.myhuaweicloud.com/v3/auth/tokens`).

- **Variable:** `RETRIEVE_URL`
  **Aliased as:** `PHYTOMNI_RETRIEVE_URL`
  **Purpose:** Document-retrieval endpoint used by KnowledgeAgent / DataAgent /
  AnalystAgent.

- **Variable:** `RERANK_URL`
  **Aliased as:** `PHYTOMNI_RERANK_URL`
  **Purpose:** Document-reranking endpoint used downstream of `RETRIEVE_URL`.

- **Variable:** `SPA_FAQ_URL`
  **Aliased as:** `PHYTOMNI_SPA_FAQ_URL`
  **Purpose:** SPA-faq lookup template; expects `{repo_id}` substitution.

- **Variable:** `DATABASE_URL`
  **Aliased as:** `PHYTOMNI_DATABASE_URL`
  **Purpose:** NL-query database endpoint; the legacy default embedded the
  workspace UUID directly inside the URL path.

- **Variable:** `ANALYSIS_URL`
  **Aliased as:** `PHYTOMNI_ANALYSIS_URL`
  **Purpose:** EI-Health workflow endpoint; the legacy default embedded project
  and job UUIDs directly inside the URL path.

- **Variable:** `OBS_SERVER`
  **Aliased as:** `PHYTOMNI_OBS_SERVER`
  **Purpose:** OBS regional host (legacy default:
  `obs.cn-east-3.myhuaweicloud.com`).

- **Variable:** `REPO_ID`
  **Aliased as:** `PHYTOMNI_REPO_ID`
  **Purpose:** Primary knowledge-repo UUID.

- **Variable:** `REPO_ID_DICT`
  **Aliased as:** `PHYTOMNI_REPO_ID_DICT`
  **Purpose:** JSON-string `{ "<repo_uuid>": <token_budget>, ... }`; parsed into
  a `Dict[str, int]` by pydantic-settings.

- **Variable:** `WORKSPACE_ID`
  **Aliased as:** `PHYTOMNI_WORKSPACE_ID`
  **Purpose:** Workspace UUID used by NL-query and analysis paths.

- **Variable:** `SUBJECT_ID`
  **Aliased as:** `PHYTOMNI_SUBJECT_ID`
  **Purpose:** NL-query database subject / schema UUID.

- **Variable:** `DATA_REPO_ID`
  **Aliased as:** `PHYTOMNI_DATA_REPO_ID`
  **Purpose:** DataAgent retrieval repo UUID.

- **Variable:** `TOOL_REPO_ID`
  **Aliased as:** `PHYTOMNI_TOOL_REPO_ID`
  **Purpose:** Analyst tool-retrieval repo UUID.

- **Variable:** `PROTOCOL_REPO_ID`
  **Aliased as:** `PHYTOMNI_PROTOCOL_REPO_ID`
  **Purpose:** DeepGenome protocol-retrieval repo UUID.

- **Variable:** `SPA_REPO_ID`
  **Aliased as:** `PHYTOMNI_SPA_REPO_ID`
  **Purpose:** DeepGenome SPA-repo UUID feeding into `SPA_FAQ_URL`.

- **Variable:** `APP_ID`
  **Aliased as:** `PHYTOMNI_APP_ID`
  **Purpose:** JSON-string `{ "small": "<uuid>", "medium": "<uuid>",`
  `"large": "<uuid>" }`; analyst compute-tier app-id map.

The aliasing matches the existing `PHYTOMNI_TLS_VERIFY` / `PHYTOMNI_CA_BUNDLE`
convention so deployments may use the prefixed form when other `PHYTOMNI_*`
variables already dominate the runtime environment. The two `Dict`-valued
entries (`REPO_ID_DICT` and `APP_ID`) ship as JSON strings (e.g.
`PHYTOMNI_REPO_ID_DICT='{"a34b...77b":128,"ec3...b":64}'`,
`PHYTOMNI_APP_ID='{"small":"<uuid>","medium":"<uuid>","large":"<uuid>"}'`) so a
single env var carries the full map.

## HTTP API Variables

- **Variable:** `API_HOST`
  **Default:** `127.0.0.1`
  **Sensitive?:** no
  **Purpose:** Uvicorn bind address.

- **Variable:** `API_PORT`
  **Default:** `8080`
  **Sensitive?:** no
  **Purpose:** Uvicorn bind port.

- **Variable:** `API_GRACEFUL_SHUTDOWN`
  **Default:** `30`
  **Sensitive?:** no
  **Purpose:** Uvicorn graceful-shutdown drain window in seconds; kept shorter
  than systemd's `TimeoutStopSec`.

- **Variable:** `API_KEYS_DB_PATH`
  **Default:** `.cache/phytomni/api_keys.sqlite`
  **Sensitive?:** no
  **Purpose:** Per-user API key SQLite store.

- **Variable:** `PHYTOMNI_API_KEYS_DB`
  **Default:** unset
  **Sensitive?:** no
  **Purpose:** Backward-compatible API key store alias.

- **Variable:** `API_TASKS_DB_PATH`
  **Default:** `server_tasks.db`
  **Sensitive?:** no
  **Purpose:** Runs and tasks SQLite store.

- **Variable:** `PHYTOMNI_TASKS_DB`
  **Default:** unset
  **Sensitive?:** no
  **Purpose:** Backward-compatible runs/tasks store alias.

- **Variable:** `MEMORY_ENABLED`
  **Default:** `false`
  **Sensitive?:** no
  **Purpose:** Opt-in user-scoped memory CRUD plus bounded read-only agent
  recall; accepts `PHYTOMNI_MEMORY_ENABLED`. Disabled deployments
  do not mount
  routes or open the memory database.

- **Variable:** `MEMORY_DB_PATH`
  **Default:** `.cache/phytomni/memory.sqlite`
  **Sensitive?:** no
  **Purpose:** Single-instance local SQLite path for memory records; accepts
  `PHYTOMNI_MEMORY_DB_PATH` and must point to a persistent local
  filesystem, never
  a network filesystem.

- **Variable:** `API_SERVICE_TOKEN`
  **Default:** unset
  **Sensitive?:** yes
  **Purpose:** Service-to-service token gating `/v1/api-keys` admin routes;
  unset disables them with `503 admin path not enabled`.

- **Variable:** `PHYTOMNI_API_SERVICE_TOKEN`
  **Default:** unset
  **Sensitive?:** yes
  **Purpose:** Backward-compatible service token alias.

- **Variable:** `API_UPLOAD_PREFIX`
  **Default:** `agent_data/uploads`
  **Sensitive?:** no
  **Purpose:** Managed OBS object-key prefix used by legacy attachment
  validation and the internal `user_uploads` projection. It is not a
  caller-controlled path and is not the resumable protocol's public
  capability.

- **Variable:** `API_UPLOAD_V2_ORIGIN`
  **Default:** `http://127.0.0.1:8080`
  **Sensitive?:** no
  **Purpose:** Absolute HTTP(S) origin used to build the resumable upload
  URL returned by `POST /v1/files`.

- **Variable:** `API_UPLOAD_V2_BUCKET`
  **Default:** `phytomni`
  **Sensitive?:** no
  **Purpose:** Bot-owned OBS bucket for resumable assets. The bucket name is
  never returned in public upload responses.

- **Variable:** `API_UPLOAD_V2_MAX_BYTES`
  **Default:** `10737418240`
  **Sensitive?:** no
  **Purpose:** Maximum resumable asset size in bytes (10 GiB); rejected
  creates return `413`.

- **Variable:** `API_UPLOAD_V2_PART_SIZE_BYTES`
  **Default:** `134217728`
  **Sensitive?:** no
  **Purpose:** Maximum part body and the default part size (128 MiB).

- **Variable:** `API_UPLOAD_V2_MAX_PARALLEL_PARTS`
  **Default:** `4`
  **Sensitive?:** no
  **Purpose:** Maximum client-recommended parallel part uploads.

- **Variable:** `API_UPLOAD_V2_CAPABILITY_TTL_SECONDS`
  **Default:** `900`
  **Sensitive?:** no
  **Purpose:** Lifetime of a browser data-plane capability, bounded to
  60-900 seconds.

- **Variable:** `API_UPLOAD_V2_SESSION_TTL_SECONDS`
  **Default:** `604800`
  **Sensitive?:** no
  **Purpose:** Lifetime of an unfinished upload session, bounded to
  1 hour-7 days.

- **Variable:** `API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS`
  **Default:** `10800`
  **Sensitive?:** no
  **Purpose:** Browser-takeover grace period for an allocated upload session,
  bounded to 60 seconds-7 days. Allocation alone is not activation evidence.

- **Variable:** `API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS`
  **Default:** `300`
  **Sensitive?:** no
  **Purpose:** Minimum interval between best-effort expired-session cleanup
  passes scheduled by native run requests.

- **Variable:** `API_UPLOAD_V2_ALLOWED_ORIGINS`
  **Default:** `[]`
  **Sensitive?:** no
  **Purpose:** Explicit browser origins allowed by the upload CORS policy;
  wildcard origins are rejected.

- **Variable:** `API_REQUEST_TIMEOUT`
  **Default:** `600.0`
  **Sensitive?:** no
  **Purpose:** Per-request timeout in seconds.

- **Variable:** `API_RATE_LIMIT_PER_MIN`
  **Default:** `120`
  **Sensitive?:** no
  **Purpose:** Per-key request budget per minute; `<= 0` disables.

- **Variable:** `API_RUN_TTL_OK_HOURS`
  **Default:** `24`
  **Sensitive?:** no
  **Purpose:** Retention for succeeded runs.

- **Variable:** `API_RUN_TTL_FAIL_DAYS`
  **Default:** `7`
  **Sensitive?:** no
  **Purpose:** Retention for failed runs.

- **Variable:** `STREAM_ANSWER_MAX_BYTES`
  **Default:** `1048576`
  **Sensitive?:** no
  **Purpose:** Soft UTF-8 byte cap for ordinary streamed-agent answer
  persistence in the run registry (1 MiB); the live SSE wire stream
  is never
  truncated. Accepts `PHYTOMNI_STREAM_ANSWER_MAX_BYTES`.

- **Variable:** `A2A_ENABLED`
  **Default:** `false`
  **Sensitive?:** no
  **Purpose:** Feature flag for the A2A v1 JSON-RPC surface; disabled by default
  and requires `A2A_PUBLIC_BASE_URL` when enabled. Accepts
  `PHYTOMNI_A2A_ENABLED`.

- **Variable:** `A2A_PUBLIC_BASE_URL`
  **Default:** `unset`
  **Sensitive?:** no
  **Purpose:** Absolute HTTP(S) public URL prefix used to build the A2A Agent
  Card and `/a2a` interface; trailing slashes are removed. Accepts
  `PHYTOMNI_A2A_PUBLIC_BASE_URL`.

- **Variable:** `INTEROP_TARGETS`
  **Default:** `[]`
  **Sensitive?:** yes
  **Purpose:** JSON array of operator-owned target definitions. It may contain
  fixed URLs or absolute stdio commands, but never headers/tokens;
  requests may
  name only a target id. Accepts `PHYTOMNI_INTEROP_TARGETS`.

- **Variable:** `MEMORY_MAX_ITEMS`
  **Default:** `100`
  **Sensitive?:** no
  **Purpose:** Maximum live records retained per user namespace; bounded to
  `1..10000`. Accepts `PHYTOMNI_MEMORY_MAX_ITEMS`.

- **Variable:** `MEMORY_MAX_CONTENT_BYTES`
  **Default:** `16384`
  **Sensitive?:** no
  **Purpose:** Maximum UTF-8 bytes in one memory record; bounded to `1..16384`.
  Accepts `PHYTOMNI_MEMORY_MAX_CONTENT_BYTES`.

- **Variable:** `MEMORY_MAX_TOTAL_BYTES`
  **Default:** `1048576`
  **Sensitive?:** no
  **Purpose:** Maximum policy-counted bytes in one user namespace; bounded to
  `1..16777216`. Accepts `PHYTOMNI_MEMORY_MAX_TOTAL_BYTES`.

- **Variable:** `MEMORY_MAX_RETRIEVAL`
  **Default:** `20`
  **Sensitive?:** no
  **Purpose:** Maximum records returned to one graph recall; bounded to
  `1..1000` and cannot exceed `MEMORY_MAX_ITEMS`. Accepts
  `PHYTOMNI_MEMORY_MAX_RETRIEVAL`.

- **Variable:** `MEMORY_GRAPH_MAX_BYTES`
  **Default:** `65536`
  **Sensitive?:** no
  **Purpose:** Maximum UTF-8 bytes returned to one graph recall; bounded to
  `1..1048576`. Accepts `PHYTOMNI_MEMORY_GRAPH_MAX_BYTES`.

- **Variable:** `INTEROP_MAX_TARGETS`
  **Default:** `64`
  **Sensitive?:** no
  **Purpose:** Maximum operator registry entries accepted at startup/lazy load;
  bounded to `1..256`, with excess entries rejected. Accepts
  `PHYTOMNI_INTEROP_MAX_TARGETS`.

- **Variable:** `INTEROP_CACHE_MAX_ENTRIES`
  **Default:** `256`
  **Sensitive?:** no
  **Purpose:** Maximum successful discovery projections kept per process;
  bounded to `1..4096`, with oldest insertion evicted first.
  Accepts
  `PHYTOMNI_INTEROP_CACHE_MAX_ENTRIES`.

- **Variable:** `A2A_MAX_HISTORY_MESSAGES`
  **Default:** `32`
  **Sensitive?:** no
  **Purpose:** Maximum messages projected by `GetTask`; bounded to `0..256`,
  where `0` disables history projection. Accepts
  `PHYTOMNI_A2A_MAX_HISTORY_MESSAGES`.

- **Variable:** `A2A_MAX_ARTIFACT_BYTES`
  **Default:** `262144`
  **Sensitive?:** no
  **Purpose:** Maximum UTF-8 bytes in one local A2A answer artifact; bounded to
  `1024..16777216`. Accepts `PHYTOMNI_A2A_MAX_ARTIFACT_BYTES`.

### Research Input Resolution Limits

Research input resolution is not feature-flagged. These four settings are
validated by the shared `ApiLimitsConfig`, accept the unprefixed or
`PHYTOMNI_` alias, and are advertised in `research_input_resolution_v1` only
when direct or relay readiness is true. Raising a lane limit cannot raise
upload concurrency, storage quota, document bytes, archive expansion, or
child fan-out.

- **Variable:** `API_MAX_USER_QUERY_CHARS`
  **Default:** `131072`
  **Hard maximum:** `1048576`
  **Sensitive?:** no
  **Purpose:** Current Research query Unicode code-point limit; prior
  conversation history remains separately bounded.

- **Variable:** `API_MAX_ATTACHMENTS_PER_REQUEST`
  **Default:** `64`
  **Hard maximum:** `256`
  **Sensitive?:** no
  **Purpose:** Request-wide managed document plus dataset reference limit.

- **Variable:** `API_MAX_RESEARCH_DATASET_PATHS`
  **Default:** `64`
  **Hard maximum:** `256`
  **Sensitive?:** no
  **Purpose:** Pasted exact-key Research dataset reference limit.

- **Variable:** `API_MAX_RESEARCH_INPUT_REFERENCES`
  **Default:** `128`
  **Hard maximum:** `256`
  **Sensitive?:** no
  **Purpose:** Combined managed and pasted Research reference limit. It must
  be at least as large as either lane limit.

The separate document conversion budgets remain 25 MiB per document and
50 MiB total converted documents. Metadata-only pasted datasets do not consume
that conversion aggregate. The canonical suffix registry supplies the
Research format catalog and longest compound-suffix classifier.

### Attachment Invocation Limits

The resumable transfer ceiling is `API_UPLOAD_V2_MAX_BYTES` (10 GiB by
default). Native agent runs and Expert routing apply a separate, deliberately
bounded attachment contract after completion:

| Limit                  | Value      | Applies to                             |
| ---------------------- | ---------- | -------------------------------------- |
| Maximum files          | 10         | One native or Expert request           |
| Maximum bytes per file | 26,214,400 | One registered document or CSV dataset |
| Maximum total bytes    | 52,428,800 | All registered uploads in one request  |

The invocation limits are inclusive; the validator rejects only values above
them. Duplicate asset ids are rejected before budget evaluation. The
owner-scoped `user_uploads` projection is stored in the SQLite database
selected by `API_TASKS_DB_PATH`; it is created only after a completed v2 asset
is resolved for an agent. `API_UPLOAD_PREFIX` remains the managed-path
boundary for legacy internal `obs_file_list` validation. Legacy preconfigured
`data_list` paths are a separate policy and are not user-upload metadata.

SQLite store defaults are relative to the service working directory. In
systemd or container deployments, set absolute paths or pin the service
working directory so restarts use the same stores.

When A2A is enabled, `/.well-known/agent-card.json` is public and `/a2a`
requires an API key with the `agents` scope plus `A2A-Version: 1.0`. Phase 2
advertises `SendMessage`, `SendStreamingMessage`, and owner-scoped `GetTask`;
the flag remains off by default so existing deployments keep their previous
route surface.

### Feature flags and flag-off rollback

All feature flags default to off. Treat a flag change as a deployment change:
update the environment, restart the API process, run the relevant smoke test,
and only then expose the route or request option to clients. Disabling a flag
removes the new surface but does not delete its local SQLite data or in-memory
run rows.

- **Surface:** A2A server
  **Enable:** `A2A_ENABLED=1` plus `A2A_PUBLIC_BASE_URL`
  **Disable / rollback:** Set `A2A_ENABLED=0`, restart, and verify the card and
  `/a2a` return `404`.
  **State retained while disabled:** Run/task rows and checkpoints; no new A2A
  requests are accepted.

- **Surface:** Outbound interop
  **Enable:** Non-empty `INTEROP_TARGETS` plus credentials
  **Disable / rollback:** Set `INTEROP_TARGETS=[]`, keep callers on
  `interop_mode=off`, restart, and verify
  `/v1/interop/capabilities` returns no peers.
  **State retained while empty:** No persistent discovery state; the process
  cache is discarded on restart.

- **Surface:** Explicit memory
  **Enable:** `MEMORY_ENABLED=1` plus a persistent local `MEMORY_DB_PATH`
  **Disable / rollback:** Set `MEMORY_ENABLED=0`, restart, and verify memory
  routes return `404`.
  **State retained while disabled:** The memory SQLite file and audit rows;
  agents stop opening the store.

- **Surface:** Credential relay
  **Enable:** `RELAY_ENABLED=1`
  **Disable / rollback:** Set `RELAY_ENABLED=0`; this kill-switch is re-read per
  request.
  **State retained while disabled:** Relay audit SQLite rows; no new relay call
  is admitted.

The flag-off path is the compatibility fallback for every opt-in surface. A
rollback must not remove or rename the corresponding database file: a later
forward upgrade may need it, and the additive migrations are designed to leave
older data readable.

### Bounded resource limits

The C6.4 limits above are safety bounds, not performance guarantees. A memory
write that would exceed the item/content/namespace policy is rejected; graph
recall is truncated to both the record and byte budgets; an oversized interop
registry is rejected; discovery cache entries are evicted oldest-first; and A2A
history/artifacts are projection caps (the underlying run answer is not
rewritten). Increasing a value requires a process restart and should be paired
with load testing and disk/RAM review. The hard ranges are enforced by
`ApiLimitsConfig`, so an out-of-range override fails startup rather than
silently weakening the boundary.

## Outbound Interoperability

Outbound MCP/A2A discovery is always mounted. Changing the target registry or
credential envelope requires an API process restart; unlike the relay
kill-switch, the registry is not re-read on each request. The first request
lazily validates the registry, and a registry failure returns `503` without
exposing the parser or secret error text. An empty `INTEROP_TARGETS` list
means no peers are configured. Request-level `interop_mode` still defaults to
`off`.

The target registry and credentials are separate. `INTEROP_TARGETS` contains
only operator-approved target policy, while the sensitive
`INTEROP_CREDENTIALS` JSON maps a `credential_ref` to a header mapping and is
loaded from `SensitiveConfig` (normally the encrypted customer envelope):

```dotenv
INTEROP_TARGETS='[{"id":"mcp-peer","kind":"mcp","transport":"streamable_http","url":"https://mcp.example.test/mcp","allowed_tools":["search"]}]'
INTEROP_CREDENTIALS='{"peer-token":{"headers":{"Authorization":"Bearer <operator-secret>"}}}'
```

The example is a shape, not a credential to copy. Never put a token, header,
password, or secret-shaped argument in `INTEROP_TARGETS`; stdio targets are
trusted operator-owned absolute binaries with fixed arguments and a minimal
environment allowlist. A request can supply neither a URL nor a command or
args — it can only refer to a configured target id.

The hardened interop HTTP client does not use environment proxies, follows no
redirects, performs no transparent retry, and injects credentials only after
the configured origin, path, TLS mode, DNS result, and IP/CIDR policy pass.
HTTPS is required unless a target explicitly opts into HTTP. Loopback,
private, link-local, special-use, and IPv4-mapped IPv6 addresses are rejected;
an explicitly configured private CIDR allowlist is the only exception. A2A
Agent Cards are structurally validated and allowlisted; without a configured
JWS trust key the service does not claim cryptographic card-signature
verification.

`INTEROP_CREDENTIALS` is intentionally absent from `ApiConfig` because it is
secret material. Rotate it by replacing the encrypted envelope (or the local
developer secret source) and restarting the process. Discovery caches keep
only sanitized capability metadata, use a monotonic per-target TTL and
single-flight concurrent requests, and do not persist an interop audit DB.
Only structured events containing target id/kind, capability, status, latency,
and stable error code are emitted; peer URLs, commands, headers, tokens, and
peer payloads are excluded.

### Research/Design request policy

The registry flag only makes operator-owned targets available; it does not
enable delegation by itself. `InSilicoResearchAgent` and
`DigitalDesignAgent` must opt in on each request with `interop_mode` and an
allowlisted `interop_targets` list:

- **`interop_mode`:** `off`
  **Request behavior:** Never discovers or invokes external MCP/A2A targets; use
  the local Analyst path.
  **Result signal:** No `metadata.interop` record.

- **`interop_mode`:** `auto`
  **Request behavior:** Attempts an eligible target, then continues locally when
  discovery, timeout, transport, or evidence collection
  fails.
  **Result signal:** `metadata.degraded_interop=true` plus a `status="degraded"`
  record when fallback occurs.

- **`interop_mode`:** `required`
  **Request behavior:** Requires bounded external evidence before local Analyst
  submission.
  **Result signal:** Failure is surfaced; the request never reports local
  pseudo-success.

An A2A `input-required` response pauses the graph before local submission and
is resumed through the normal HTTP/MCP run resume adapter. Formatted metadata
contains only `target_id`, `kind`, `capability`, `status`, and `latency_ms`;
protocol correlation ids remain in sanitized intermediate state for debug
inspection and are not caller-provided controls.

## Relay Variables

These tune the credential-injecting relay (`/v1/relay/*`). The relay
reuses the existing upstream endpoints (see *Deployment Endpoints and
UUIDs*) and operator secrets (see *Encrypted Customer Envelope*) — there
is no relay-specific secret. Every variable accepts an unprefixed or
`PHYTOMNI_RELAY_*` alias.

- **Variable:** `RELAY_ENABLED`
  **Default:** `false`
  **Sensitive?:** no
  **Purpose:** Expose `/v1/relay/*`; re-read every request so a disable is an
  instant kill-switch.

- **Variable:** `RELAY_AUDIT_DB_PATH`
  **Default:** `.cache/phytomni/relay_audit.sqlite`
  **Sensitive?:** no
  **Purpose:** Local relay audit SQLite store; keep on a local disk (WAL
  deadlocks on network filesystems).

- **Variable:** `RELAY_AUDIT_RETENTION_DAYS`
  **Default:** `90`
  **Sensitive?:** no
  **Purpose:** Age in days after which audit rows are eligible for cleanup.

- **Variable:** `RELAY_REQUEST_MAX_BYTES`
  **Default:** `10485760`
  **Sensitive?:** no
  **Purpose:** Max relayed request body in bytes; over-limit returns `413`
  without buffering the whole body.

- **Variable:** `RELAY_REQUEST_AUDIT_MAX_BYTES`
  **Default:** `65536`
  **Sensitive?:** no
  **Purpose:** Max UTF-8 bytes retained in the sanitized request-body audit
  copy; separate from the client-facing request cap.

- **Variable:** `RELAY_RESPONSE_AUDIT_MAX_BYTES`
  **Default:** `10485760`
  **Sensitive?:** no
  **Purpose:** Max upstream response bytes copied into the audit; the
  client-facing response is never truncated.

- **Variable:** `RELAY_RESPONSE_MAX_BYTES`
  **Default:** `1073741824`
  **Sensitive?:** no
  **Purpose:** Max OBS object size the download relay streams back before
  returning `413`; distinct from the request-body cap.

- **Variable:** `RELAY_TIMEOUT_SECONDS`
  **Default:** `600.0`
  **Sensitive?:** no
  **Purpose:** Per-request upstream timeout and the total wall-clock ceiling for
  a streamed forward.

- **Variable:** `RELAY_RATE_LIMIT_PER_MIN`
  **Default:** `60`
  **Sensitive?:** no
  **Purpose:** Per-key relay request budget per minute (separate from
  `API_RATE_LIMIT_PER_MIN`); over-limit `429`.

- **Variable:** `RELAY_MAX_CONCURRENT_PER_KEY`
  **Default:** `8`
  **Sensitive?:** no
  **Purpose:** Max in-flight relay forwards per key; excess returns `503`.

Rate, concurrency, and retention state are per worker, so the effective
per-key ceilings scale with the worker count. See
`docs/ops/http-api-runbook.md` *Relay Operations* for the operator
procedures and `docs/reference/http-api.md` *Relay* for the route contracts.

## Cache and Registry Variables

- **Variable:** `PHYTOMNI_CACHE_DB`
  **Default:** `.cache/phytomni/func_cache.sqlite`
  **Purpose:** Function cache SQLite path.

- **Variable:** `PHYTOMNI_TESTING`
  **Default:** unset
  **Purpose:** Set to `1` only in tests to disable real `.env` loading.

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

- **Variable:** `PHYTOMNI_TLS_VERIFY`
  **Default:** `true`
  **Sensitive?:** no
  **Purpose:** Disable peer certificate verification when set to
  `0`/`false`/`no` (dev or pinned on-prem only).

- **Variable:** `PHYTOMNI_CA_BUNDLE`
  **Default:** unset
  **Sensitive?:** no
  **Purpose:** Absolute path to a PEM CA bundle. Honoured when verification is
  on; ignored when verification is off.

Every async HTTP call in `mcp_server_phytomni` should flow through
`common.httpx_client.get_async_client`, which reads these settings once
per call and hands the resolved `verify` argument to `httpx.AsyncClient`.
A missing or unreadable `PHYTOMNI_CA_BUNDLE` path surfaces as the same
`ssl.SSLError` the underlying SDK would emit; flip
`PHYTOMNI_TLS_VERIFY=0` in dev environments behind a corporate proxy or
self-signed cluster ingress only — production deployments should ship a
real CA bundle instead.

## Server Tuning Variables

- **Variable:** `TIMEOUT`
  **Default:** `600.0`
  **Sensitive?:** no
  **Purpose:** Generic agent/provider request timeout; separate from local
  polling and remote job ceilings. The synchronous Agent configuration
  classes override this repository default to match the Web-owned business
  budgets:

  | Agent      | Config class      | Default (seconds) |
  | ---------- | ----------------- | ----------------: |
  | Chat       | `ChatConfig`      |              3000 |
  | Knowledge  | `KnowledgeConfig` |             15000 |
  | Data       | `DataConfig`      |              9000 |
  | Review     | `ReviewConfig`    |             30000 |
  | Brief Gene | `BriefGeneConfig` |             30000 |

  Analyst and its background-task descendants retain the generic 600-second
  provider budget; `MAX_POLL` and `ANALYSIS_JOB_TIMEOUT` remain their separate
  86400-second polling and remote-job ceilings. In child relay mode, the five
  synchronous Agents send an allowlisted internal timeout profile. The relay
  applies the matching class value to its connect/read timeout and streaming
  deadline, strips the profile before forwarding upstream, and keeps
  `RELAY_TIMEOUT_SECONDS=600` for unknown profiles, unknown models, and
  non-LLM services. A deployment-level `TIMEOUT` setting still overrides the
  selected class field through the existing Pydantic settings contract.

- **Variable:** `MAX_POLL`
  **Default:** `86400`
  **Sensitive?:** no
  **Purpose:** Maximum local polling duration for long-running
  Analyst/DeepGenome work.

- **Variable:** `ANALYSIS_JOB_TIMEOUT`
  **Default:** `86400`
  **Sensitive?:** no
  **Purpose:** Maximum duration sent to the remote analysis platform for one
  submitted job.

- **Variable:** `GAUSS_COMMAND_TIMEOUT`
  **Default:** `30.0`
  **Sensitive?:** no
  **Purpose:** Per-query timeout in seconds for the direct GaussDB pool
  (`agents/shared/gauss.py`).

- **Variable:** `HTTP_MAX_CONNECTIONS`
  **Default:** `100`
  **Sensitive?:** no
  **Purpose:** Max total connections for each runtime-owned trusted/direct
  `httpx.AsyncClient` pool (`runtime/outbound/http.py`).

- **Variable:** `HTTP_MAX_KEEPALIVE`
  **Default:** `50`
  **Sensitive?:** no
  **Purpose:** Max keepalive connections for each runtime-owned
  trusted/direct `httpx.AsyncClient` pool (`runtime/outbound/http.py`).

These are `ServerConfig` fields read once at startup. `GAUSS_COMMAND_TIMEOUT`
bounds each direct GaussDB query so a stuck backend cannot hold a pooled
connection indefinitely; `HTTP_MAX_CONNECTIONS` / `HTTP_MAX_KEEPALIVE` set
the `httpx.Limits` handed to the shared `AsyncClient` so heavy
`multi_retrieve` x `rerank` x relay fan-out reuses connections instead of
churning TCP/TLS handshakes. Leave them unset to accept the defaults.

## Outbound Logical Pool Variables

The following 13 required `ServerConfig` variables define independent,
logical per-process request budgets: 12 service capacities plus the wait
warning threshold. Every variable also accepts a `PHYTOMNI_`-prefixed alias.
A capacity of `0` is unlimited; positive values use an AnyIO capacity limiter.
These budgets neither configure HTTP socket pools nor impose request
timeouts, and a full pool waits until a borrower releases its lease.

- **Variable:** `OUTBOUND_LLM_CONCURRENCY`
- **Variable:** `OUTBOUND_RETRIEVAL_CONCURRENCY`
- **Variable:** `OUTBOUND_RERANK_CONCURRENCY`
- **Variable:** `OUTBOUND_NL2SQL_CONCURRENCY`
- **Variable:** `OUTBOUND_ANALYSIS_CONTROL_CONCURRENCY`
- **Variable:** `OUTBOUND_ANALYSIS_STATUS_CONCURRENCY`
- **Variable:** `OUTBOUND_IAM_CONCURRENCY`
- **Variable:** `OUTBOUND_SPA_FAQ_CONCURRENCY`
- **Variable:** `OUTBOUND_BI_CONCURRENCY`
- **Variable:** `OUTBOUND_OBS_CONCURRENCY`
- **Variable:** `OUTBOUND_RELAY_CONTROL_CONCURRENCY`
- **Variable:** `OUTBOUND_INTEROP_CONCURRENCY`

Each capacity is a required integer greater than or equal to `0` and is not
sensitive. LLM completion and stream requests share the LLM pool. The
capacity is per process: a deployment with `N` worker processes or replicas
can admit up to `N * capacity` borrowers for a positive setting. Size each
pool from the provider quota and expected request duration, then multiply the
per-process budget when reviewing the deployment-wide limit.

- **Variable:** `OUTBOUND_POOL_WAIT_WARN_SECONDS`
  **Purpose:** Required finite positive threshold for a value-safe pool-wait
  warning; the warning contains only the fixed pool name and numeric counters.

These logical budgets do not replace `HTTP_MAX_CONNECTIONS` or
`HTTP_MAX_KEEPALIVE`. The HTTP settings remain physical limits on each active
trusted, direct-upstream, or OpenAI-owned client profile, so the possible
sum of profile connections is still part of deployment sizing. No endpoint,
global socket semaphore, or metric API is added by the logical pools.

## Live E2E Variables

- **Variable:** `PHYTOMNI_RUN_INTEGRATION`
  **Default:** unset
  **Purpose:** Set to `1` to allow integration tests.

- **Variable:** `PHYTOMNI_ALLOW_NETWORK`
  **Default:** unset
  **Purpose:** Set to `1` to allow network tests.

- **Variable:** `PHYTOMNI_RUN_OUTBOUND_POOL_E2E`
  **Default:** unset
  **Purpose:** Set to `1` to enable the outbound-pooling live acceptance
  module after the other safety gates pass.

- **Variable:** `PHYTOMNI_CONFIRM_NON_PRODUCTION`
  **Default:** unset
  **Purpose:** Set to `1` only for a time-bounded run against disposable,
  explicitly non-production services and objects. It is not a production
  deployment or activation approval.

- **Variable:** `PHYTOMNI_OUTBOUND_POOL_E2E_CHAT_TIMEOUT_SECONDS`
  **Default:** `3600`
  **Purpose:** Large but finite read timeout for the outbound-pooling Chat API
  probe; accepted range is `1` through `7200` seconds.

The four safety gates authorize only the three currently executable live
scenarios (`llm_stream_and_completion`, `retrieval_and_rerank`, and `nl2sql`).
They do not imply that analysis, relay, OBS, or Interop targets are disposable
or non-production. The runbook lists every unavailable scenario as
`external-pending`; operators must supply the named authority before running
it, and the missing gated harness must be implemented first. Never substitute
a production service.

- **Variable:** `PHYTOMNI_E2E_SUBMIT_TIMEOUT_SECONDS`
  **Default:** `1800`
  **Purpose:** Submit timeout for async tool calls.

- **Variable:** `PHYTOMNI_E2E_POLL_TIMEOUT_SECONDS`
  **Default:** `600`
  **Purpose:** Polling deadline for one async task.

- **Variable:** `PHYTOMNI_E2E_TASKS_DB`
  **Default:** `server_tasks.db`
  **Purpose:** Override the task registry used by e2e polling.

- **Variable:** `PHYTOMNI_E2E_RUN_KA_UPLOAD`
  **Default:** unset
  **Purpose:** Set to `1` to include the slow KnowledgeAgent upload variant.

- **Variable:** `PHYTOMNI_E2E_API_STARTUP_SECONDS`
  **Default:** `120`
  **Purpose:** HTTP API e2e startup health-gate budget.

- **Variable:** `PHYTOMNI_E2E_API_READ_TIMEOUT_SECONDS`
  **Default:** `1200`
  **Purpose:** HTTP API e2e per-request read timeout.

## Do Not Commit

- Plaintext `.env`
- `.env.encrypted` generated for a real customer unless explicitly intended
  for that distribution workflow
- `.license_key`
- API keys, OBS credentials, model keys, or copied customer tokens
- Local SQLite registries, cache databases, WAL/SHM files, and virtual
  environments
