# Deployment and Storage

This document covers runtime configuration, encrypted customer
distribution, OBSFS-first storage, scratch paths, and startup
troubleshooting.

## Local Configuration

Copy the example environment file and fill in real credentials:

```bash
cp src/mcp_server_phytomni/config/.env.example \
  src/mcp_server_phytomni/config/.env
```

Expected variables:

```bash
DOMAIN_NAME=your_domain_name
USER_NAME=your_username
USER_PASSWORD=your_password
ACCESS_KEY_ID=your_access_key_id
SECRET_ACCESS_KEY=your_secret_access_key
BASE_URL=your_llm_base_url
MODEL_ID=your_model_id
API_KEY=your_api_key
CODER_URL=your_coder_base_url
CODER_MODEL=your_coder_model
CODER_API_KEY=your_coder_api_key
EMBED_URL=your_embed_base_url
EMBED_MODEL=your_embed_model
EMBED_API_KEY=your_embed_api_key
BI_TOKEN=your_bi_token
```

`EMBED_URL`, `EMBED_MODEL`, and `EMBED_API_KEY` are required. `BI_TOKEN`
is optional and defaults to empty. Legacy `AccessKeyID` and
`SecretAccessKey` names remain accepted for compatibility, but new local
configuration should use the uppercase names.

Never commit `.env`, API keys, OBS credentials, model keys, generated cache
databases, local SQLite registries, or local virtual environments.

## Distribution to Trusted Customers

Phytomni-Bot ships to trusted customers as a Docker image that consumes the
operator's Huawei resources and LLM quota. The plaintext `.env` must never
enter the image. Instead, each customer gets a per-customer encrypted
envelope.

Build-time operator step:

```bash
python scripts/encrypt_env.py \
  --input src/mcp_server_phytomni/config/.env \
  --license-key "<per-customer-license-key>" \
  --output src/mcp_server_phytomni/config/.env.encrypted
```

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
1. A license key plus `.env.encrypted`: customer-image path.
1. A plaintext `config/.env`: local developer path.

A wrong `PHYTOMNI_LICENSE_KEY` or corrupted envelope raises
`SecretEnvelopeError` and aborts startup rather than booting with empty
secrets.
