# Security Policy

## Supported Versions

| Python version | Supported |
| -------------- | --------- |
| 3.14           | yes       |
| 3.13           | yes       |
| 3.12           | yes       |
| < 3.12         | no        |

Phytomni-Bot targets Python `>=3.12,<3.15`. Security fixes land on
`main`; there is no separate maintenance branch.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately to the maintainer:

**Shang Xie — <xieshang0608@gmail.com>**

Do not open a public GitHub issue for a vulnerability report. Include
enough detail to reproduce the issue and, if known, its impact. You
should expect an initial response acknowledging the report; a fix
timeline depends on severity and complexity.

## Security Posture

- **Customer secret distribution.** Customer images ship an
  AES-256-GCM `.env.encrypted` envelope rather than a plaintext
  `.env`. The envelope is decrypted at process startup using a license
  key delivered out-of-band, and `.dockerignore` excludes plaintext
  `.env` and `.license_key` from build contexts so they cannot enter
  an image. See [docs/guides/deployment.md](docs/guides/deployment.md)
  and [docs/reference/configuration.md](docs/reference/configuration.md)
  for the full distribution and secret-resolution model.
- **CI secret scanning.** Every push and the full tracked tree are
  scanned for committed secrets by
  [`.github/workflows/secret-scan.yml`](.github/workflows/secret-scan.yml).
- **API key hashing.** HTTP API keys are stored as salted
  PBKDF2-HMAC-SHA256 hashes; the plaintext key is shown once at
  creation and is not recoverable afterward.
- **Response sanitization.** The MCP dispatch boundary strips
  credential-pattern keys from the diagnostic `raw` response block
  before it can reach a client.
- **Secret typing.** Sensitive configuration fields are carried as
  Pydantic `SecretStr`, which keeps them out of default string
  representations and accidental log output.

## Scope

This policy covers the `mcp_server_phytomni` and `mcp_client_phytomni`
packages in this repository. Vulnerabilities in upstream dependencies
should be reported to their respective maintainers.
