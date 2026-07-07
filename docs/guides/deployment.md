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

## Distribution to Trusted Customers

Phytomni-Bot ships to trusted customers as a Docker image that consumes the
operator's Huawei resources and LLM quota. The plaintext `.env` must never
enter the image. Instead, each customer gets a per-customer encrypted
envelope. (The plaintext-first resolution order in
[configuration.md](../reference/configuration.md#resolution-order) is dev-side only;
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
[configuration.md](../reference/configuration.md#encrypted-customer-envelope) for the
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
