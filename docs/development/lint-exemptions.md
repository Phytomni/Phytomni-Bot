# Static-analysis exemption ledger

This file is generated from `static-analysis-exemptions.toml`.

Regeneration:

```bash
uv run python scripts/check_static_analysis_exemptions.py render-docs
```

- Schema version: `1`
- Policy default: `deny`
- Authorized records: `4`

## Informational counts

| Tool and rule                   | Records |
| ------------------------------- | ------: |
| `pylint:R0801`                  |       2 |
| `pylint:too-few-public-methods` |       1 |
| `pylint:too-many-ancestors`     |       1 |

## Exact records

| ID | Tool | Rule | Classification | Mechanism | Target | Path | Symbol | Fingerprint | Owner | Introduced | Review | Expiry | Remediation | Tests |
| \--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `SAE-STR-0001` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/common/http.py | AsyncRequestClient | `sha256:5a0e264d2208fa111ebbb5a63bcd2a561210f8a9c7343df19f2be929589131ee` | bot-maintainers | 2026-08-11 | 2026-09-11 | — | — | tests/unit/test_http_retries.py, tests/unit/test_http_errors.py |
| `SAE-STR-0002` | pylint | too-many-ancestors | structural | inline | symbol | src/mcp_server_phytomni/config/models/agents.py | DeepGenomeConfig | `sha256:596114738e14622538df779ab79f77247e93239fd31b1695f0b69c7f048191f5` | bot-maintainers | 2026-08-11 | 2026-09-11 | — | — | tests/unit/config/test_defaults.py, tests/server/test_handler_support.py |
| `SAE-STR-0007` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 49:62 | `sha256:538e3fef2c779c4f6b238b89de68cf7dc15f41d8bc6fe90a39d776969dc2e0bc` | bot-maintainers | 2026-08-11 | 2026-09-11 | — | — | tests/unit/config/test_required_env.py |
| `SAE-STR-0008` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/mcp/result_formatting.py | 41:51 | `sha256:5c60b7b540adeb7af7facaf7f9ea44eddd885c2fb9ccd78b0851621a168d5c2c` | bot-maintainers | 2026-08-11 | 2026-09-11 | — | — | tests/server/test_api_chat_streaming_context.py, tests/unit/test_result_formatting_projection.py |

## Review fields

### `SAE-STR-0001`

Rationale:

```text
The one-method request Protocol intentionally describes the narrow dependency used by retry helpers.
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
DeepGenomeConfig intentionally composes the DataConfig and AnalystConfig field surfaces for existing agent consumers.
```

Counterfactual:

```text
Split the model into a new shared configuration base and migrate every consumer.
```

Risk:

```text
Changing the inheritance contract can alter Pydantic field and validator resolution.
```

### `SAE-STR-0007`

Rationale:

```text
The required outbound field inventory is intentionally mirrored by the required-env contract test.
```

Counterfactual:

```text
Generate the test assertion from the production inventory.
```

Risk:

```text
A production field change can require two updates without this diagnostic.
```

### `SAE-STR-0008`

Rationale:

```text
The bounded context-staged event payload is intentionally represented in both public formatting and service types.
```

Counterfactual:

```text
Centralize the event payload type and update both consumers.
```

Risk:

```text
Context metadata fields can drift between the two representations.
```
