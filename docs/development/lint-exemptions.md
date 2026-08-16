# Static-analysis exemption ledger

This file is generated from `static-analysis-exemptions.toml`.

Regeneration:

```bash
uv run python scripts/check_static_analysis_exemptions.py render-docs
```

- Schema version: `1`
- Policy default: `deny`
- Authorized records: `2`

## Informational counts

| Tool and rule                   | Records |
| ------------------------------- | ------: |
| `pylint:too-few-public-methods` |       1 |
| `pylint:too-many-ancestors`     |       1 |

## Exact records

### `SAE-STR-0001`

- Tool:
  `pylint`
- Rule:
  `too-few-public-methods`
- Classification:
  `structural`
- Mechanism:
  `inline`
- Target:
  `symbol`
- Path:
  `src/mcp_server_phytomni/common/http.py`
- Symbol:
  `AsyncRequestClient`
- Fingerprint:
  `sha256:5a0e264d2208fa111ebbb5a63bcd2a561210f8a9c7343df19f2be929589131ee`
- Owner:
  `bot-maintainers`
- Introduced:
  `2026-08-11`
- Review:
  `2026-09-11`
- Expiry:
  `—`
- Remediation:
  `—`
- Tests:
  - `tests/unit/test_http_retries.py`
  - `tests/unit/test_http_errors.py`

### `SAE-STR-0002`

- Tool:
  `pylint`
- Rule:
  `too-many-ancestors`
- Classification:
  `structural`
- Mechanism:
  `inline`
- Target:
  `symbol`
- Path:
  `src/mcp_server_phytomni/config/models/agents.py`
- Symbol:
  `DeepGenomeConfig`
- Fingerprint:
  `sha256:596114738e14622538df779ab79f77247e93239fd31b1695f0b69c7f048191f5`
- Owner:
  `bot-maintainers`
- Introduced:
  `2026-08-11`
- Review:
  `2026-09-11`
- Expiry:
  `—`
- Remediation:
  `—`
- Tests:
  - `tests/unit/config/test_defaults.py`
  - `tests/server/test_handler_support.py`

## Review fields

### `SAE-STR-0001`

Rationale:

```text
The one-method request Protocol intentionally describes the narrow dependency
used by retry helpers.
```

Counterfactual:

```text
Replace the structural Protocol with a broader client abstraction.
```

Risk:

```text
A broadening of the retry helper contract could be hidden by this exemption.
```

### `SAE-STR-0002`

Rationale:

```text
DeepGenomeConfig intentionally composes the DataConfig and AnalystConfig field
surfaces for existing agent consumers.
```

Counterfactual:

```text
Split the model into a new shared configuration base and migrate every
consumer.
```

Risk:

```text
Changing the inheritance contract can alter Pydantic field and validator
resolution.
```
