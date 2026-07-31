# Deployment and Storage

This document covers customer distribution, OBSFS-first storage, scratch
paths, and startup troubleshooting. The canonical variable matrix lives in
[Configuration](../reference/configuration.md).

## Local Configuration

Copy the example environment file and fill in real credentials:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

See [Configuration](../reference/configuration.md) for the required variables,
accepted legacy aliases, and runtime-only customer license key behavior.

Never commit `.env`, API keys, OBS credentials, model keys, generated cache
databases, local SQLite registries, or local virtual environments.

## Upgrading an existing deployment

For the `0.1.2` → `0.1.3` rollout, follow the complete
[Upgrade Notes](../ops/upgrading.md) before replacing the running wheel or
image. It covers the stop-before-backup sequence, version/OpenAPI smoke,
native `/v1/agents` capability discovery, additive SQLite state, and rollback
ordering. The [HTTP API Operations Runbook](../ops/http-api-runbook.md) remains
the source for health checks, feature-specific smoke tests, and multi-worker
limitations.

The 0.1.3 A2UI, A2A, outbound interop, explicit memory, and credential-relay
surfaces remain disabled by default. A Bot-local gate does not close Web/Go,
DBA, operations, live-backend, or production acceptance; enable a surface
only after its owner returns the corresponding redacted evidence.

## Outbound Interoperability Trust Boundary

Outbound MCP/A2A discovery is an operator feature and is disabled by default.
Enable it explicitly in the API environment, then restart the API process:

```dotenv
INTEROP_ENABLED=1
INTEROP_TARGETS='[{"id":"mcp-peer","kind":"mcp","transport":"streamable_http","url":"https://mcp.example.test/mcp","allowed_tools":["search"]}]'
```

Keep `INTEROP_TARGETS` and the sensitive `INTEROP_CREDENTIALS` mapping
separate. The registry may contain fixed origins or absolute stdio command
paths plus policy, but never a header, token, password, or secret-shaped
argument. Store the credential mapping in the encrypted customer envelope (or
the local developer secret source), refer to it by `credential_ref`, and
restart after rotation. Requests can name only a configured target id; callers
cannot supply a URL, command, args, or credential.

Treat each stdio target as an explicit code-execution grant to the service
account. Review the absolute binary, fixed arguments, file permissions, and
minimal environment allowlist (`LANG`, `LC_ALL`, `PATH`, `TMPDIR`) before
enabling it. Run the API under a dedicated unprivileged account and keep
`NoNewPrivileges=true` / a restricted `ReadWritePaths` policy in systemd or
the equivalent container sandbox.

HTTP/A2A targets use a separate hardened client rather than the trusted
backend connection pool. It ignores environment proxy variables, follows no
redirects, performs no transparent retry, pins each request to a freshly
validated DNS address while preserving Host/SNI, and injects credentials only
after origin/path/TLS checks pass. HTTPS is required unless the target opts
into HTTP. Loopback, private, link-local, special-use, and IPv4-mapped IPv6
addresses are rejected; a private address is usable only through an explicit
target CIDR allowlist. A2A cards are structurally validated and origin/skill
allowlisted. Without a JWS trust key, do not describe the card as
cryptographically signed or verified.

The read-only `GET /v1/interop/capabilities` endpoint requires an API key with
the `agents` scope and is mounted only while the flag is enabled. It performs
discovery but never executes a remote tool or agent. Successful metadata is
cached in process memory per target using monotonic TTL and single-flight;
errors are isolated to the target and not long-term negative-cached. The
response and structured logs exclude endpoints, commands, headers, tokens, and
peer payloads. There is no persistent interop audit database. Research/Design
delegation is available only when a request explicitly sets
`interop_mode=auto|required` and names operator-registered `interop_targets`;
the default `off` mode remains local-only. `auto` records a degraded local
fallback when no evidence is returned, while `required` fails closed. The
formatted response exposes only bounded target/kind/capability/status/latency
metadata, and A2A input-required pauses use the existing run resume path.

## Distribution to Trusted Customers

Phytomni-Bot ships to trusted customers as a Docker image that consumes the
operator's Huawei resources and LLM quota. The plaintext `.env` must never
enter the image. Instead, each customer gets a per-customer encrypted
envelope. (The plaintext-first resolution order in
[configuration.md](../reference/configuration.md#resolution-order) is dev-side
only;
`.dockerignore` blocks plaintext `.env` from build contexts so customer
images reach the encrypted fallback unconditionally.)

Build-time operator step:

```bash
python scripts/encrypt_env.py \
  --input src/mcp_server_phytomni/config/.env \
  --license-key "<per-customer-license-key>" \
  --output src/mcp_server_phytomni/config/.env.encrypted
```

The input `.env` must be valid UTF-8 **without** a byte-order mark (BOM).
`encrypt_env.py` rejects GBK / ANSI / BOM-encoded input with a clear error
and exit code `4`, so a mis-encoded source file (common on Chinese Windows
build hosts) cannot be sealed into an image — where it would otherwise
surface as a cryptic `UnicodeDecodeError` at customer startup. See
[configuration.md](../reference/configuration.md#encrypted-customer-envelope)
for the
full encoding contract.

The output is an AES-256-GCM blob with `PHYBOT01` magic and a
PBKDF2-derived key. Bake `.env.encrypted` into the customer's image, never
the plaintext `.env`.

Runtime customer step:

- Provide `PHYTOMNI_LICENSE_KEY` as an environment variable, or
- provide a `config/.license_key` file mounted or dropped onto the host.

The environment variable wins when both are present. The server derives the
key, decrypts the envelope into the process environment at startup, and does
not write plaintext secrets back to disk.

The license key is delivered out-of-band and must not be baked into the
image. `.dockerignore` and `.gitignore` exclude `.license_key` just as they
exclude plaintext `.env`.

A leaked license key compromises one customer's envelope only. Rebuild and
redistribute with a rotated key.

Encryption blocks casual inspection through `docker history`,
`docker export`, and direct file reads. It does not protect against a
motivated operator with process-memory or traffic access on their own host.

## OBSFS Storage

Storage helpers prefer the obsfs mount at `/obs/phytomni` for OBS-backed
file operations. When that mount or an individual filesystem operation is
not usable, the code falls back to the existing OBS SDK path and
credentials. There is no feature flag; availability is detected at runtime.

When obsfs is available:

- Uploaded documents are converted directly from the mounted source path.
- Generated Analyst metadata is written under
  `/obs/phytomni/agent_data/tmp_data/`.
- DeepGenome reads completed Analyst result directories in place instead of
  downloading them to local staging.

Per-run scratch directories are resolved through `storage/scratch.py`.
Handler-level temporary file roots, Analyst download caches, DeepGenome
downloaded-result directories, and synthesized-report directories land under:

```text
/obs/phytomni/agent_data/user_data/<user>/runs/<date>/<run>/<scope>/{downloads,tmp}/
```

Without obsfs they fall back to run-scoped subdirectories of `TEMP_DIR`,
`DOWNLOAD_PATH`, or `DEEPGENOME_OUT`.

Use `storage.scratch.resolve_scratch_dir` for new agent-level call sites and
`mcp.handlers.scratch_server_dir` for new handler wrappers.

For root or sudo-enabled runtime checks:

```bash
sudo stat /obs/phytomni
sudo test -r /obs/phytomni && sudo test -w /obs/phytomni
```

If those checks fail, normal execution should still work through the OBS SDK
fallback as long as the configured OBS credentials are valid.

## Missing Configuration

If no configuration source is found, startup raises a `RuntimeError` that
enumerates the accepted provisioning paths:

1. `PHYTOMNI_TESTING=1`: test suites inject dummy secrets.
1. A plaintext `config/.env`: local developer path.
1. A license key plus `.env.encrypted`: customer-image fallback.

A wrong `PHYTOMNI_LICENSE_KEY`, a corrupted envelope, or an envelope sealed
from a non-UTF-8 / BOM-prefixed `.env` raises `SecretEnvelopeError` and
aborts startup rather than booting with empty or mangled secrets. The
encoding case is the build-side foot-gun the `encrypt_env.py` UTF-8 guard
prevents for new artifacts; an already-shipped bad envelope must be rebuilt
from a UTF-8 (no-BOM) source.
