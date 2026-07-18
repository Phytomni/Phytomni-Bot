# Static-analysis exemption ledger

This file is generated from `static-analysis-exemptions.toml`.

Regeneration:

```bash
uv run python scripts/check_static_analysis_exemptions.py render-docs
```

- Schema version: `1`
- Policy default: `deny`
- Authorized records: `277`

## Informational counts

| Tool and rule                                                     | Records |
| ----------------------------------------------------------------- | ------: |
| `flake8:E203`                                                     |       1 |
| `flake8:W503`                                                     |       1 |
| `mypy:ignore_missing_imports`                                     |       2 |
| `mypy:misc`                                                       |       2 |
| `mypy:prop-decorator`                                             |       1 |
| `pylint:R0801`                                                    |     152 |
| `pylint:R0903`                                                    |      29 |
| `pylint:broad-exception-caught`                                   |       6 |
| `pylint:contextmanager-generator-missing-cleanup`                 |       2 |
| `pylint:max-module-lines`                                         |       1 |
| `pylint:path-ignore`                                              |       5 |
| `pylint:protected-access`                                         |      22 |
| `pylint:too-few-public-methods`                                   |       3 |
| `pylint:too-many-arguments`                                       |       9 |
| `pylint:too-many-instance-attributes`                             |       3 |
| `pylint:too-many-lines`                                           |       3 |
| `pylint:too-many-locals`                                          |      12 |
| `pylint:too-many-positional-arguments`                            |       2 |
| `pylint:too-many-statements`                                      |       1 |
| `pylint:wrong-import-position`                                    |       1 |
| `pymarkdown:md013`                                                |       1 |
| `pytest:error`                                                    |       1 |
| `pytest:ignore:ssl.PROTOCOL_TLS is deprecated:DeprecationWarning` |       1 |
| `ruff:ASYNC109`                                                   |       1 |
| `ruff:ASYNC110`                                                   |       3 |
| `ruff:ASYNC240`                                                   |       7 |
| `ruff:E402`                                                       |       2 |
| `ruff:N802`                                                       |       1 |
| `ruff:N803`                                                       |       1 |
| `ruff:N815`                                                       |       1 |

## Exact records

| ID | Tool | Rule | Classification | Mechanism | Target | Path | Symbol | Fingerprint | Owner | Introduced | Review | Expiry | Remediation | Tests |
| \--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `SAE-STR-0001` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | DeepGenomeProfileMixin | `sha256:f919078a50df4db511f41d14935848003d67ff934117be82f1f237cc9b4cf6f4` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_cache_candidates.py, tests/agents/test_deep_genome_sql.py |
| `SAE-STR-0002` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/report.py | DeepGenomeReportMixin | `sha256:4b34b5b2a359f15b41ff6c5f9b12ab49cb33631f7f5f1982518b4458f4ac89e5` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_deep_genome_chat_subgraph.py, tests/agents/test_deep_genome_knowledge_subgraph.py, tests/agents/test_deep_genome_lifecycle.py, tests/agents/test_deep_genome_report.py |
| `SAE-STR-0003` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/review/report.py | ReviewReportMixin | `sha256:1437a6aeed48bfca0a47bfe423e9f70a2258c20f3ae7e558805dfc7cc19af8a8` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_review_add_query_failures.py, tests/agents/test_review_report_helpers.py, tests/agents/test_review_revised_fan_out.py |
| `SAE-TMP-0001` | flake8 | E203 | temporary | config | config | .flake8 | [flake8].extend-ignore | `sha256:ee3ff2f285462681d7e4dd5eed33cbf4cd5ef1d73ca986281e086dc5ef2457a8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0002` | flake8 | W503 | temporary | config | config | .flake8 | [flake8].extend-ignore | `sha256:1d93293eb8e91ba241c13a2db0c100dc24b5305c4b0f429faa5ca2f334bfccc1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0006` | mypy | ignore_missing_imports | temporary | config | config | pyproject.toml | tool.mypy.overrides[0].ignore_missing_imports | `sha256:527e6efc74769b1228c507a1b8b80620eb8ac8b9870681cad036239346b48e93` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0007` | mypy | ignore_missing_imports | temporary | config | config | pyproject.toml | tool.mypy.overrides[1].ignore_missing_imports | `sha256:34f322754c32f50efaf7a19013545c63f95683102c5f7eefefb2e6fdabd638c2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0009` | mypy | misc | temporary | inline | symbol | tests/unit/interop/test_capabilities.py | test_capability_is_frozen_and_qualified_name_is_canonical | `sha256:50c5686aeb514f0ea385b0f5efcb20543fb02b479f0a05b4b32116c2e38f65fe` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0010` | mypy | misc | temporary | inline | symbol | tests/unit/test_deep_genome_store.py | test_store_exports_frozen_contract_models | `sha256:da11d568e3816c864121904ffa5d00103600ebf471ec6a5bbefc12277ad4761c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0011` | mypy | prop-decorator | temporary | inline | symbol | src/mcp_server_phytomni/api/schemas.py | FileUploadResponse | `sha256:243d7b24f3e529af946567903305f2f5f09ad24b6316e1063a7ed27a93107a6c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0012` | pylint | R0801 | temporary | diagnostic | pair | e2e/helpers/polling.py | 336:345 | `sha256:cddaaaa04f92a9fe57ff67e2ce0d4a24501907412ac56b79f71c5a4260150509` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0013` | pylint | R0801 | temporary | diagnostic | pair | e2e/helpers/polling.py | 575:581 | `sha256:ef92dfc802b7295423a28a5b01598f3b642888f8ce21bbffbdaf6e62e2dae3c1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0014` | pylint | R0801 | temporary | diagnostic | pair | scripts/compare_gauss_queries.py | 294:301 | `sha256:ca2b6d468bc318f627b0fe79e4d2116a77ed25e26cc4ec75aff51feb8b8aab15` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0015` | pylint | R0801 | temporary | diagnostic | pair | scripts/compare_gauss_queries.py | 91:99 | `sha256:9f9f3a0f8627709ab7819ee411220c1052aca195cca63666c9e6cb61722409cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0017` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/helpers.py | 17:25 | `sha256:2bde4ceca2e89f4d0dad6f8725a9c3d0c698832307b62142e73dbd56081c53ec` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0018` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/helpers.py | 19:25 | `sha256:c3d8f0f3bfc38a05eedccf0a9944fa14e3875058532624f30f85eb678652d56c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0019` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 341:349 | `sha256:290742346aa7c7c34844303e39f49a3a4c084e52891b671d3920b8c4dd1b9dc7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0020` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 342:348 | `sha256:b1101f1725b92758ac965df2e52c683ebb72eff13f3b3441df98302e87b3eb5e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0021` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 101:115 | `sha256:c87f948de5cea76c38e86b5c43164ae82e0768d8fd10d71b9dd6dce1af1ed334` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0022` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/source.py | 84:98 | `sha256:67feab30f45f9e474e0c786410eb52f9e3d687f54cc727e088b39882af857abd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0024` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_client_phytomni/http_client.py | 19:30 | `sha256:f52f7ed0f6653cde4b0ea03422f8fb7718558d2a4e875fa5bf2dc24a7a0bd91a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0025` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_client_phytomni/http_client.py | 19:32 | `sha256:8d4e94b19ed6a2d6acaf204a042d913ff743672ba85397bdc3858346fa355fcd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0026` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_client_phytomni/http_client.py | 22:32 | `sha256:0c9fe821cd42964efeb7bddee529465755dae050e0b18086624799fa7552e367` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0027` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_client_phytomni/http_client.py | 278:288 | `sha256:c8759585f97690eb52cf5af8aec0c20c3f0e711d9cafb32317690f9eeec46e2d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0028` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/__init__.py | 42:47 | `sha256:04b1f52c7567afcb17be02df79c0cadd82b90a00fa8a07e06ab48b5a2dafafb8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0029` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/agent.py | 39:44 | `sha256:db1a39a7296629412f62345cacd4a0f73d49c06d2acf3b459fe10a81dcf460dc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0030` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/core.py | 107:113 | `sha256:fb6e27c1684c510fcf236b861bc91ebd92559e45b9e5136b2fffc917c859cd83` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0031` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/core.py | 178:187 | `sha256:1d5b8c90a4526dccbfbd591ede421e591b88150a115e07976c11832136044e44` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0032` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/core.py | 234:244 | `sha256:5797dc0093813262892fe4d0b78be2f990a7a576bd4b844540d12cd2592c3e77` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0033` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/core.py | 457:463 | `sha256:baddf42588bca4446d3eb1e7102c40d9f5f00b15116258e0a5aa5dbdb055235e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0034` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/core.py | 500:509 | `sha256:00540296658667b0db8451f55e02e422f72522c0fca8a856b38a602915a8ef55` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0035` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/graph_chat_subgraph.py | 112:122 | `sha256:565a256502cfe4e73e0768809f450852215aad8bdf6936c3cd4433078c037174` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0036` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/graph_chat_subgraph.py | 114:121 | `sha256:f8d15ccac20acbf337cb053b0ded4b0c3103e110e88be02f8e9b0e6a70e92dbf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0037` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/graph_chat_subgraph.py | 114:122 | `sha256:df636d63e90b624510fdad0c8130a2268202e15295c52dbcb72293b42568f70e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0038` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/graph_chat_subgraph.py | 373:381 | `sha256:46dbfac4d78a2c8e9ff0e0a65325fa3462e5d9ea4d2faf66e9016e4f0fd467a1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0039` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/graph_chat_subgraph.py | 555:583 | `sha256:a8e2eeb566bf1de32c539245d489884a5fa162eef25b65e00638de2ddaac938f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0040` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/planning.py | 101:107 | `sha256:aa9f030fa22f1341c154c9cfa1b703bee67e2fcb6e2aa47ea744319bd9039090` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0041` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/state.py | 114:119 | `sha256:b9c858b4756d1eeba0e3575fea150ea4750ceadcf06fb842cd871d087c87a349` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0042` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 119:124 | `sha256:672ac8ba2ca15e4a6bff483256212bad9a4b452b5b2f47f58d4c71a953af2ffb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0043` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 119:130 | `sha256:0a201031a7234bed17589e4e095b273be77befb070f165201474ca57d8733796` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0044` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 131:138 | `sha256:d2eb8102471c388d5ab72cb5468a375af44240897171a2a20c19bb8db477b15e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0045` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 264:277 | `sha256:71c51e7737eb6d61c69c8b052130cf106fa57d55217488c3520076465b9f19af` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0046` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 81:87 | `sha256:4e2efd035ff8c8489702efc9cd2c5821e6c43082be5bf553df9c94d573d2e4c0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0047` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 88:95 | `sha256:207ed8d656c295eeb6fb6750c816cbed9878039f84fb4aa015d69afff7a66abc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0048` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/resolve_query.py | 172:182 | `sha256:22c58e79ce545bc10e4774f09c0e67172a5f0ebc78bef0ac7dad1c90708fb2bf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0049` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/resolve_query.py | 234:262 | `sha256:0081df1d9daeeb0d18bd417b93f290abbda31bd956894413bbfc245dee1d9190` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0050` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/resolve_query.py | 269:284 | `sha256:e77a387a221646f3099cfba8d658c6f9b821f7930d87d2066b947756d9ee18b9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0051` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/resolve_query.py | 93:100 | `sha256:f6e6aecb155f6cc129c21888c259c70958bbc2696f1e0581f6c53a2818882b7b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0052` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 106:111 | `sha256:efda7a4d0bc9f7c0bc21f3ab1a9de39e3f2e5d7a57d98bb90cc13a61be50d4e3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0053` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 130:143 | `sha256:30b55bc914bb205817b22f0e14d035412ee8a0181e7bc80de269c2e309350dba` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0054` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 61:67 | `sha256:7a8fa01108e449558ae8f535f73b355b23a912c1574bac86cd8013fb45d656c5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0055` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 67:76 | `sha256:61a59518f847e0f2c50b3f7a5e2810830159c6e7c0f5e22d05d98b35dc754895` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0056` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/chat/a2ui_graph.py | 197:207 | `sha256:c2d9b397b9cf9fdf2daec3d5a3281cc05f7c974d14a938108a47e972dc4c9292` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0057` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/data/agent.py | 220:230 | `sha256:65eddedd74aa40ca4074fabf24d427ec56302be3fcad5c153dd081b9c1a54327` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0058` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/data/agent.py | 261:271 | `sha256:4f968cc4e8fae819dfd4eb099d32955d8fecb4ab8529cd76b6c0a040b8f458ef` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0059` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/data/agent.py | 261:274 | `sha256:67e3f7e920faa4c5565eecbd04ae955f65fad55ab92b2e69836511be429005b3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0060` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/deep_genome/agent.py | 89:109 | `sha256:b4b963480d858629b532c499ad4623327af6699366b96afb4f757636f3fce0d2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0061` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | 172:181 | `sha256:2c939f306bfc192d1b4ccf2554102891e23b77d00d43855201693dcb676bfc6b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0062` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 373:380 | `sha256:3c8069cc1e54f7418478fe01918e775f22ded1f6ac84a477a72872605e5d25e3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0063` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 501:513 | `sha256:e4338944c597b8c1519ad7ba8adab4416a20627173a8a2f833be300debd30149` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0064` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 553:562 | `sha256:ae438b318722e000d2af31f2c1812f8dbc8d60a336c9b40649bfb53b1e5370cb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0065` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 560:574 | `sha256:47596af147e9172828fdf5f2c27d8408eb421b1192fc39ef3d33c6befabe7607` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0066` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 563:579 | `sha256:7271d0ac0f1454361e1f47819895b51d59eed4d6a85d5b0608b5b4ab0584fa2e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0067` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 659:689 | `sha256:f4dd4186f6a6b658a87efdd1a26751b119ce33cdfc6ee2a2f14b9e188c081185` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0068` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/environment/agent.py | 89:96 | `sha256:48e20f923448194a6bdb689cd13d84a61c3719f98bd21ce267c78d548b3679d0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0069` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/environment/graph.py | 171:178 | `sha256:acecf18e51ca4896d2aae066f303e97fcbacb43d1cacb6108164dcfc959ab015` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0070` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/research/interop.py | 238:245 | `sha256:5100104b2803ff4bfed6614c72a1e1bdd6c12912c00e954b9f70e3317834d541` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0071` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/agents/shared/chat_subgraph.py | 93:99 | `sha256:6165ee88078541c3c33262bd4a723df6d7bdd0feacd68b2c48d2e63bd71b3045` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0072` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/api/auth.py | 258:285 | `sha256:48767ad66c60c12e2c4e02c5e52f1b602a1f507b290cb63f106d52c5bfcca527` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0073` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/api/relay/__init__.py | 20:25 | `sha256:eec99e9b196bfe6a37ddc06107d9d5e5ac8401f4403c900f626c613979bf1d1d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0074` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/api/relay/audit_filter.py | 27:38 | `sha256:e403f78e603f501a9592111cdd3963a7670f069c42cb5d1e1755b764d1dc32e9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0075` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 16:28 | `sha256:0a106c71d1e188f33ba116a1a15980da2ccbd8924042f13b31d0dd5df3f2585c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0076` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 41:46 | `sha256:b181294b667a5a5e3b3cc601d177d1d5e50806e12df472c917d370454afa17f9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0077` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/graphs/analyst_to_knowledge_adapters.py | 51:78 | `sha256:e14253a7a9e9e7b71513ae4cb71a3357c7df362eae880ee7ed1320287287495e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0078` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/mcp/app.py | 776:781 | `sha256:4a57d42c7066ead11e72824504ea38c35100a972dea9d50d624994daf75dfac4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0079` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/mcp/result_formatting.py | 1168:1175 | `sha256:39bb04ba061c5c096516cd49aa3d965d902986d4ef683a93996c50dbee9472aa` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0080` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/mcp/result_formatting.py | 38:49 | `sha256:f5522e6938b71179fb0baf2e7eb8c4294d3891dd03b2475e5156e869a9a62647` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0081` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/mcp/result_formatting.py | 922:932 | `sha256:5f656e069097320802fd3f0e8c41eb074bbbb6e62e9b5ed3fdc65efcc021facc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0082` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/runtime/deep_genome_report_snapshot.py | 27:35 | `sha256:ada3e86401e92e3bfc426af9b7194848762b2b4052c8e1df325025167c13594a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0083` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 45:52 | `sha256:781cbd4798bbd051136fe6306e69070b99da317486e9f110b66c4c53f764ebd8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0084` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 59:64 | `sha256:dab91daad1dc692da89b2d3c4bedfcd081201a6cd5e592f6d9f289f0ad825f66` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0085` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 73:79 | `sha256:02a0ef0f7f5041c275b28c1c4c24c010eae62e1de902e15478e70af3b9aa9b42` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0086` | pylint | R0801 | temporary | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/migrations.py | 23:31 | `sha256:4f06fd5ec9e45a0c63ea3f65946f56998710e5123f0135fc1bc20ca49b9bbe3a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0087` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_graph_io.py | 31:59 | `sha256:00329e540fcf919758aeee18da87364144040e6e9ad4949b263c9ce419990c7f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0088` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_graph_io.py | 36:59 | `sha256:fc3966432000e2768da9529c49a2577c3c4ca9df46275cd8d13c76df0d63360c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0089` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_knowledge_subgraph.py | 196:226 | `sha256:cebca18db252e1274c9969546a7c44b772adccbcf0ad0ccff0c7ed2d075bb55c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0090` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_knowledge_subgraph.py | 240:247 | `sha256:8dc884b580cce5c39ad8a2802c18b2a35afee9972599cb5cebc208f90751304c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0091` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_knowledge_subgraph.py | 32:74 | `sha256:7800fab6ac8dfd4f5a375ae6bda76a0eba526e1a229e6858d9e25f0c58d564bd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0092` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_brief_gene_chat_subgraph.py | 48:56 | `sha256:6dd6be1ef0509a12985128efe55b9da11d7f79526e1d11f7cc278526cd3ca149` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0093` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_brief_gene_knowledge_subgraph.py | 115:120 | `sha256:f720cfe1651fe48ee40f96b8c484a8f11c851e959e66b6e9be3bd04c06b1ddc2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0094` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_chat_adapters.py | 41:79 | `sha256:25447e820df5b0bcb2bcb2bb5e0e95d8e992ebcb5e49488b0884e9c52be06f8e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0095` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_data_chat_subgraph.py | 67:95 | `sha256:ffe9cc993869c90076e42992f7bcbdb3a3286688c2bdc31b1fb1e0c8da395532` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0096` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_design_mount.py | 506:515 | `sha256:379d69b825614f6c4622544ca3554760a749338730b42d6702a8b994879180dd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0097` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_design_mount.py | 50:76 | `sha256:e81b26339ab9fe82dca21c97fe9d4b632d8466cc9d3bfe86a6a82950a9a6d178` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0098` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_design_mount.py | 528:538 | `sha256:071ce086a928f64d2ad3e849e3b6548346aa988cfe1ab79589bd375821d6c640` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0099` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_design_mount.py | 80:85 | `sha256:400841f01e9d8e3cb49687e6da6007ce61df9ead8fd96f286be1b280420ddd34` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0100` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 205:215 | `sha256:75dad3aced8db3b4e246aadf053950e52fe4a7cdd5fc8d42f880ce982eead563` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0101` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 232:242 | `sha256:fa56f98abe19802582ed18c6218bdb33f8ac06548e017c481da1b514a67217dd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0102` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 243:252 | `sha256:bd1b158bf7ec303dbeb32e2d61e567458d5bbaf4412f870fa26884e744803ac5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0103` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_design_promoter_design_for_gene.py | 25:41 | `sha256:e6595d7854d763a159f27ce0b85baeebbd21b540066e083d1818cae0089c8a04` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0104` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_design_promoter_design_for_gene.py | 42:51 | `sha256:80ce2a5ec0ac26365188bffc84ec81380b06f8c129f9b6c1cb508d774072a02d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0105` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_design_promoter_design_for_gene.py | 53:68 | `sha256:d86600bc49af4e7a4b294acc0acd55f56a5e5d60c17eb4646ea5e12f364dc7f8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0106` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_design_promoter_design_for_gene.py | 92:99 | `sha256:5759fe9e8fdad269e6284f32c139e153687cf90150f83cec1f58fdc63f507cf9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0107` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_environment_agent.py | 168:176 | `sha256:438efc993803215209e6e887b6f6b354dcbbba01f357c07cbb55e20f8f4ec120` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0108` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_graphs_manifest_export.py | 178:193 | `sha256:04db7ecea1e9da5bd8060fda5b6806090e1033f622c378c61e05ab9f03af601b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0109` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_resume_kernel.py | 25:42 | `sha256:4ad39b4ad4b4823fe799020de33c01bfd5161991446aff20e5be60d87f550eae` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0110` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_resume_kernel.py | 43:48 | `sha256:e44e55cdded49e86109ef57098c78ebda86bba0f2946ece45fa0ec9ed1c314db` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0111` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_chat_single_shot.py | 156:164 | `sha256:0fb9d3d39c674c4d3fdc8042975b3c408da75a1d41d6e5601ad54debbdc238fb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0112` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_chat_single_shot.py | 23:45 | `sha256:17aa8f6b430636750e69deaacd9ba86f302cc05511adb9f5f9e7bb3a6bbdf77a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0113` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_chat_single_shot.py | 31:51 | `sha256:3bbed7e3f201e004a105288208b210cdd40983c918f420a34379393ee960ac50` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0114` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_chat_single_shot.py | 62:69 | `sha256:444834c3f901a5305b3c3e34c7ccadf4de4ca7b806ba2307b60a568c8b317f06` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0115` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_chat_single_shot.py | 86:98 | `sha256:12920a86211612f44ce8762893dfc346eed9152baba1a219b472514fa43fc50f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0116` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 114:120 | `sha256:8cc4e58c389216e39fa93d306063adf59953674ed0e220a287025591ac845ee1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0117` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 130:153 | `sha256:92ec8f32f7d749843eb1c6a4d9459827bb993ee6af8c2c5895de221b7497f814` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0118` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 147:153 | `sha256:f215e11b7c898654df2e7aebd01fbeefbd10d719ac4a84ffb4cf9ea20182d9bb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0119` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 163:169 | `sha256:eaf188fba7b5830a586378fe2af515e5c826370bff3ceae4999296c48899e0bb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0120` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 28:57 | `sha256:987ab1b0fe2dc6f9fdd6ccc45625f03b7c4f59e3dbbc749b4a71fad2a7b19b65` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0121` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_draft_fan_out.py | 73:83 | `sha256:a6e790af2012c9fdbd05696bfe6ce8ddce94f1b95defffe90dfab43a8c40ec8a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0122` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_follow_up_routing.py | 167:180 | `sha256:9ef10dc699ee41f949ea96d13d1b7a4539c981f6553d848d2b15a86da8c1a999` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0123` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_review_results_fan_out.py | 279:284 | `sha256:e367559e17eb5bb4fed426c744bc59f989a3cdfd2bbefe1c5daf7a64b65e102a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0124` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_review_review_results_fan_out.py | 53:59 | `sha256:2d77b4ebe51ab6654b88ac04d35fc03397a5b828eacccf77fb331af184602115` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0125` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_a2ui_actions_http.py | 810:819 | `sha256:d30623a9d2caa6c8f0ea3c64da0ad0d8b8d7ed8e0e45dfa8300d9198f8efbda1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0138` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_agent_runs.py | 279:284 | `sha256:6e4751d43a63d7aff887cb68161f78e9be2f4202e5b99f4d158cb5adfd00fd5e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0139` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_brief_gene_resolve.py | 342:357 | `sha256:e2deb1873295a49e527fba53ee268c30a673457090dd41c46cbeae5725b71ce4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0140` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 233:239 | `sha256:5e9a1039956c911bbc89be0fbed03a680579c6d59449b2a50a6f0f8d69d80a0b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0141` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 296:302 | `sha256:b6e4f4decd5768fc08e5ada11b43d227a6818ddc8050b25464b07ad0228b3df8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0142` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 296:303 | `sha256:0a3135c9e77ac6764bbc391c5d874cc0dac2fbb58bc1e9e6426ee4cc7e8fe799` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0143` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 299:306 | `sha256:771d1eb24f3c86febc0a2c17fb106d2c2a21dcd514b38c5f140523e90b48d7fd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0144` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 301:313 | `sha256:67471d49d473bc65d93d39d0d378bade70b59c76e4d621e0f38da5c9287394e6` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0145` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 30:38 | `sha256:b7af15f4cb9c241f09a4c1c07f2dcfeb1ab95d31e950e1e9efdc2656a2078656` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0146` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 40:60 | `sha256:2977e3498d50907554e3fc4077b98454c8772c81a95e3e75fd2cf58959b7adc2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0147` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_deep_genome_resolve.py | 191:209 | `sha256:e32ad3fc650616a8da64ba403cca619b880e6f6fe7c935a57547b2234a733980` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0148` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_deep_genome_resolve.py | 274:283 | `sha256:83449b870f9b68f0f652d5fb432a566b53db9d96deb2a7437be85435ed69a00f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0149` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_deep_genome_resolve.py | 58:86 | `sha256:877674888b12eb2791b5c8558bf13c318c34dc8564cdf760310197e99896e0d1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0150` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_run_logs.py | 266:280 | `sha256:a50099a104216d972c604e94c37fd02ad914cb71b6efddf52a2497c4b71c2afe` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0151` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_runs_list.py | 549:558 | `sha256:45d985de66ab2e70b9b99e7ad5f272c4dc35e629d0716f0b6c4236318e6c28a5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0152` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_user_context.py | 40:51 | `sha256:d070c5d26bcb1a3a76655010cc1e2066842679987463a6520c2e4f896470af51` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0153` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_get_task_status.py | 39:49 | `sha256:6c428246112060c3705f0bd8095bd141da283c3bd9c6ceda5da9009c926a6cbd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0154` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_get_task_status.py | 54:60 | `sha256:652a691371dabbdf328a2b9333b29731804925679f1fef3dbdeb7aad806e1e49` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0155` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_mcp_app_invoke.py | 266:272 | `sha256:2ffab28ca78e35f68ffce562e642663c93ef659e6d1e80fc65e9e7c5271ad3bf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0156` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_mcp_app_invoke.py | 304:317 | `sha256:f6175ef13c21d05adb80c45ca307f25cfbf594357c0c7e86bf6d2a44d7001726` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0157` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_memory_http.py | 39:44 | `sha256:53523ba52adeb85599e4208c9fb09c7547bf6b963993602ada44250a976677cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0158` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_forward_request.py | 64:74 | `sha256:eb7c75d69effca7d17ae04846f5feb2d7ac869090b92381125ac43d1aab36f37` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0159` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_obs_routes.py | 49:54 | `sha256:c50d4d55fd65c090198f5e50a55ff28a38127f0cfa53ba459a00df3bd8da9dbc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0160` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_obs_routes.py | 67:79 | `sha256:41c0b03586d07944630aaf83607bbca72966b829ee988c0a6e94e4d51542656e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0161` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_openai_routes.py | 76:111 | `sha256:90d03cbc6cfa912e6c79ba754c576d80abf09250ca899f3d180038494cc00bfb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0162` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_result_formatting.py | 479:488 | `sha256:fde2a886b5be06310ff9328180822b646f6ede9217907f7a98a46f408026b52f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0163` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_resume_mcp.py | 107:117 | `sha256:0984e98111b5f90c75d8d316dd689e5eb68ffe7657805d1240a8f724d01610cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0164` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/e2e/test_assertions.py | 21:29 | `sha256:db5380a79499711c2d976b02d7d205221fd26de599f39b7eb7406e68420c5f2c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0165` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/e2e/test_assertions.py | 31:36 | `sha256:08f087169cfa40da6f218af8475e416e1f72b4db4385297134263b558270e1e8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0166` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 126:132 | `sha256:ecf2324d0c1b39b2f5ab6c1ebb468afc9262b3a3dee182cd9a7ccd2bf6cca2a7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0167` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 133:141 | `sha256:2c326a038c82a3e01dbe2752e30ce9836eebc95688bd6ae65fcefb2711623434` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0168` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 293:306 | `sha256:eb3e93560d9c6607712d8955e1858b2e712515c029d2c14977024abe120064e5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0169` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 87:96 | `sha256:79e228ba92766d992ece14a5cc1de66540efa4031b0094d106a707ac6dbede6c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0170` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_brief_gene_resolve_query.py | 367:374 | `sha256:a0aa8303dfa11019175b5a3ab850b87ff714178c1ec022b762f235a78a782021` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0171` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_client_elicitation.py | 59:66 | `sha256:975f355cd6c2a65dca9d57186615c419eeda303eac5e94c7fdd0adfb91f399cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0172` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_admin.py | 38:46 | `sha256:d31703b04d8b444d3e963b1cc94534a6a2a39ec56fe231841e5ec923d2b20c11` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0173` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_admin.py | 41:48 | `sha256:5125884f84d157e2c66e315e2fa2e58b8a8b9d1bd8c812d3b7e98eca06f64ee4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0174` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_dispatch.py | 97:103 | `sha256:31820c51b0a514a097e2db7ad223b448b4d3807e19d96c12138e1c010da5cc0e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0175` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_store.py | 36:47 | `sha256:c745b76ae3ca2d6d7f4539177b39872d80bcf54b0b9d11bec0acbc5dfa2d7ee3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0176` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_store.py | 49:62 | `sha256:838d045a66d38a592d0d88e73bb8fdfd07daf6af2726fc2a439e63f69209d0fa` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0177` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_stream_lifecycle.py | 228:241 | `sha256:ac967fd4db89089be6f80ebe53d9d8dce90582d0d91a72ac1c8c1c9a10d7bff6` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0186` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_brief_gene_knowledge_subgraph.py | test_retrieve_worker_factory_exception_writes_empty_sentinel.\_BrokenApp | `sha256:34a3bbaa6e71f356df4c01429dc47c184701680974c839900abe92d31e90c893` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0187` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_brief_gene_preamble_workflow.py | \_StubKnowledgeApp | `sha256:f1dbcf14aa38debc6423bb6268245a639a685dd62bf2f582aaf4e7a31f79d3ca` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0188` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_chat_agent.py | test_non_streaming_repairs_reasoning_content_answer_tail.MisplacedReasoningCompletion | `sha256:2f75398d04db82e71f2cf4f463d5cdd72b935714ee1128b7f189fbcf289bca4a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0189` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_deep_genome_brief_gene_mount.py | test_brief_gene_failure_does_not_invoke_downstream_submit.\_BrokenApp | `sha256:b58783ca3413f5f76def9ffaef0b161b7f0d08ee4ed343e35c349f56d6b822b7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0190` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_deep_genome_brief_gene_mount.py | test_brief_gene_mount_raises_required_error_on_failure.\_BrokenApp | `sha256:31334abe5cddacacbc063c40fb2f07aead798035460567b94c078d09c851ba37` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0193` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_deep_genome_mount_redaction.py | test_brief_gene_mount_uses_fixed_public_error.\_BrokenApp | `sha256:26c1f14d2299360b28e651119cfe184ef8fbb9207f88d0f9b8c2d681c77c6455` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0194` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_deep_genome_submit.py | \_FakeApp | `sha256:1f7f51708ece122db06fccc8c565c771ccda09fd3dab04360277d25248e3fef0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0195` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_evolution_agent.py | test_find_spa_taxids_returns_empty_on_non_200.fake_factory.\_Client | `sha256:30d84cc563351bd457a2c3432421d275b52d37cbcb4eb47ad2995978940b5c99` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0196` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_evolution_agent.py | test_find_spa_taxids_uses_async_httpx_factory.fake_factory.\_Client | `sha256:c31799112ea227e85aea5e4d06a3f01ee32ea862dc6052b0a3288c34f7b127de` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0197` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_knowledge_subgraph_helper.py | \_FakeKnowledgeApp | `sha256:ff37e230f44b4b65165722510e09b07e53ed8acbe51c4687d0261c03f3de3278` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0198` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_relay_routing.py | \_FakeCompletion | `sha256:7e651d3dbe9e31cf8551870e01d06dbbb2dbe4cd1535577c4b3cb490e690e918` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0199` | pylint | R0903 | temporary | diagnostic | symbol | tests/agents/test_stream_graph_agent.py | test_graph_stream_propagates_runtime_failure.FailingStreamApp | `sha256:b0465f55f3e6f8e0e7b66876e97cf548b39448f8f11acc850e204f4659f4794c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0200` | pylint | R0903 | temporary | diagnostic | symbol | tests/conftest.py | \_build_fake_obs_client.\_FakeObsClient | `sha256:bdc751aa42890e90315050767a71d97be294611f7284929d9a7335ee0932b470` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0201` | pylint | R0903 | temporary | diagnostic | symbol | tests/server/test_a2ui_chat_streaming.py | test_stream_a2ui_runtime_failure_emits_error_and_fails_run.\_FailingA2UIApp | `sha256:c63479d4ed42935fc72a4ad099d0f8f61d49ed48e95fd766f8280971f84365ce` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0205` | pylint | R0903 | temporary | diagnostic | symbol | tests/server/test_a2ui_review_http.py | test_review_stream_runtime_failure_emits_error_and_fails_run.\_FailingReviewApp | `sha256:8ce4faa6d5b78b445ff5d54cc0b9d2107239fa3f69e7939dc959655d98e14b98` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0206` | pylint | R0903 | temporary | diagnostic | symbol | tests/server/test_handler_support.py | \_FakeSecret | `sha256:a1bd544f1ccb72e7949fea2654de9b9fdfbecd6784921616fde3ca8a748c7a65` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0209` | pylint | R0903 | temporary | diagnostic | symbol | tests/server/test_resume_mcp.py | \_FakeInterrupt | `sha256:035f11516f396fb8e888a46558f2a978da954672237aa30dfa2422c6bf3e3e7e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0210` | pylint | R0903 | temporary | diagnostic | symbol | tests/server/test_resume_mcp.py | \_FakeSession.elicit.\_Result | `sha256:f9eb44d6ebcf74c3171819ecb6bb1f006ac372c7acbe613deca70779ea3d84b4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0211` | pylint | R0903 | temporary | diagnostic | symbol | tests/unit/e2e/test_polling.py | test_http_poll_records_distinct_monotonic_revisions.Client | `sha256:da7cf65686cb7478e57df8ba489482a275c62ee25fd5a4da92b82dd36292c5b3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0212` | pylint | R0903 | temporary | diagnostic | symbol | tests/unit/e2e/test_polling.py | test_http_poll_records_distinct_monotonic_revisions.Response | `sha256:03ead9da23231e283f93b93411a8248714edbce30f10c7e3923b833abd38f33f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0213` | pylint | R0903 | temporary | diagnostic | symbol | tests/unit/interop/test_fake_peer_e2e.py | \_FakeMCPServer | `sha256:54f65c95838ec87a9421e2969ae36697b1040eb1ee33b2309074ef73ed4c7025` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0214` | pylint | R0903 | temporary | diagnostic | symbol | tests/unit/test_api_file_upload.py | \_ChunkedUpload | `sha256:19158fcb6dfafa4294b516da1f6fb89fb68038bc14f3cc96f097a31b5d763163` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0215` | pylint | R0903 | temporary | diagnostic | symbol | tests/unit/test_storage_error_sanitization.py | \_ExplodingObsClient | `sha256:d30fc310f840684a257ef77723b2705404b3e66b7e58cd8185c9f33400e56633` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0216` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | GetObjectHeader | `sha256:8b33ef3b41e1734cd85d4db326c94556f6c6710caf36d9587a9cdd22b2d67edc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0217` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | ListObjectsBody | `sha256:27ea6a3da40442bb712fc570308e704e944c4af45f746c28767d5c67ffe8f77e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0218` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | ListObjectsResponse | `sha256:f650d953e8dfca4242cadee1342e6a2cf1f646c895af830e03904febff1ca070` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0219` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | ObjectSummary | `sha256:2cf1097236098490dea6ba9ab35034d84aaa518f3980cac0d8de72bd1a93c830` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0220` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | ObsResponse | `sha256:86477f709254d56c91dea64df5319161ffe7b5377e92dffa9911285e74c34411` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0221` | pylint | R0903 | temporary | diagnostic | symbol | typings/obs/__init__.pyi | PutObjectHeader | `sha256:f57100dd30cfc38f37a226a4092a97b7df38f3b10625cbf08caae5f436fae090` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0222` | pylint | broad-exception-caught | temporary | inline | symbol | e2e/helpers/polling.py | \_reconciled_task_state | `sha256:58b3b2f3bb4ee5b58ee8ca5380146a1ab5d43c18bff5e36510cb912eece854a3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0223` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_maybe_project_review_interrupt | `sha256:00283b9c5b01c64fbb2fbbd8564f6f8a84317997b467d41ffdac50f678c5fde0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0224` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_chat_a2ui_confirm | `sha256:ff5cde0a7f9152ec9aa42901ceea6d7b91c6f9ec6805aaabc0ccf64c0182b166` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0225` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_chat_completion | `sha256:3c57338060cbf808466bcfb818b3ffd44c40c522cd6590962e809e287a53a3e3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0226` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_review_a2ui_pause | `sha256:007aee7350c14cf87de6a37f48a6e20593841d7097b4adeeaa113022232c86a3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0227` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/mcp/stream_lifecycle.py | project_stream_failures | `sha256:0fa2621f7153da1bff3a4df5a0a23d14425aa3d17b782f9545d0c2e657296557` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0228` | pylint | contextmanager-generator-missing-cleanup | temporary | inline | span | tests/agents/test_cache_candidates.py | — | `sha256:1a93e1b05a8cfa872aeddc39c7850189f9089c16b59a0d36c97909cc4341ae03` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0229` | pylint | contextmanager-generator-missing-cleanup | temporary | inline | span | tests/conftest.py | — | `sha256:8051cd3af754f03cba8b655218a54168e8867d393d6ed998ce98de611d0b7b9a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0232` | pylint | max-module-lines | temporary | config | config | pyproject.toml | tool.pylint.format.max-module-lines | `sha256:56ab2666391bfe20e49b31efb25773c9fa08523cec25d451bac69ed8516cd17f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0235` | pylint | path-ignore | temporary | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:2594f4f66df475f4c21526ce505b0814d4b10599e889277db5d89ee8b92bbb1e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0236` | pylint | path-ignore | temporary | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:38fd2748eed7f3803e3176f0ef992ae35b8441cfc99a8e873d01821f62c700eb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0237` | pylint | path-ignore | temporary | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:3e1bc269c16210ee4149f6c708497a514291d2716f41ad7186c731fe6fce7ea3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0238` | pylint | path-ignore | temporary | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:829ec7992def978f01ad8efae0a0ee64c5e1767cfeb76f3a13236dc4ed5cef23` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0239` | pylint | path-ignore | temporary | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:b7c41d52ad3e667583ed5bc27c3cffee33f6c621371befb228c17357718fc94a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0241` | pylint | protected-access | temporary | inline | span | tests/agents/test_analyst_graph_nodes.py | — | `sha256:0b0a232e2a85cdff21bf4f66e78588404106ab1579a3d5a58d949bce8d7fe870` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0242` | pylint | protected-access | temporary | inline | span | tests/agents/test_analyst_knowledge_subgraph.py | — | `sha256:184e1ebaae891611aee25ac7732145405af2529f5b9a7fc3635d1456e49f80d8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0243` | pylint | protected-access | temporary | inline | span | tests/agents/test_brief_gene_knowledge_subgraph.py | — | `sha256:53b920f718f4267c30e4435f07abf4f533cd2acdaf6cca3f43885ce683c39263` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0244` | pylint | protected-access | temporary | inline | span | tests/agents/test_data_knowledge_subgraph.py | — | `sha256:7355080d432ad83c419cad3b4d8a2fa587787b015bc2f4abc0f7a7859f3c57d4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0245` | pylint | protected-access | temporary | inline | span | tests/agents/test_deep_genome_chat_subgraph.py | — | `sha256:a906df6ac3ba8be7f7354116b6ca50d358ef31583f6cd609769ae94bfee39137` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0246` | pylint | protected-access | temporary | inline | span | tests/agents/test_deep_genome_dispatch_routing.py | — | `sha256:e6817e4d57f379047aacfb24dc75936fe81b2bde08a36424f9ef6debb0e29f2d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0247` | pylint | protected-access | temporary | inline | span | tests/agents/test_deep_genome_knowledge_subgraph.py | — | `sha256:e1558d6b07c52c2260fbcdbf5f1ec89aa3b19e9e8dbb00ae2db7707435259169` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0248` | pylint | protected-access | temporary | inline | span | tests/agents/test_deep_genome_lifecycle.py | — | `sha256:cfadadd6fdc9f05d4d99269d2993a555360be047c5bc050004e5e1abd0a7d059` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0249` | pylint | protected-access | temporary | inline | span | tests/agents/test_design_analyst_subgraph.py | — | `sha256:9be8391b3a06b2bdd95ceab37deae647bcb665c80f89f07087624ecd96f2a49a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0250` | pylint | protected-access | temporary | inline | span | tests/agents/test_design_helpers.py | — | `sha256:226dc5e2a223b35941a225d0f01cb600f1b87a5092aa69992c93c9a1fc31d4c4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0251` | pylint | protected-access | temporary | inline | span | tests/agents/test_network_analyst_subgraph.py | — | `sha256:a9086de22445f0443cb2bfe90759a60fe34151cb4dbd18c3ce1a921cef7e33c1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0252` | pylint | protected-access | temporary | inline | span | tests/agents/test_research_analyst_subgraph.py | — | `sha256:a92f8f7fd5f8bce81bd9260081741954094599d0ef816da156d3e029025d9bc0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0253` | pylint | protected-access | temporary | inline | span | tests/agents/test_research_chat_subgraph.py | — | `sha256:15b62a607f4a5f73cf0fc7520b9a737915c6443f4052ceba17a34161da641433` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0254` | pylint | protected-access | temporary | inline | span | tests/agents/test_resolver_schema_derivation.py | — | `sha256:df9d3948a546c09b6e17bf86f3ab3e899addb1c3bb8dd2cc218508be66ee6a10` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0255` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_add_query_failures.py | — | `sha256:54358a4844d2a88c0ccd4e20d42a819c4cd4a7a8c43c3b0fa1eea860cc42ee00` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0256` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_draft_fan_out.py | — | `sha256:65c71f673b4427cbb4efa9e04be5afcb73d456ddd446555f30fa091e6161de0b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0257` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_follow_up_routing.py | — | `sha256:1f2cfee40b9fcfbdec115990c0bc26088f67802fa3d170226bd59a8993abd658` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0258` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_retrieve_fan_out.py | — | `sha256:3d6f49c940c1a38e47a9ee960babcfe29ff913cff2328f5e01908d7c0e833a18` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0259` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_review_results_fan_out.py | — | `sha256:5d8401dd8146d69f1d85248402d93f143a53cbc1d258fbfb0b378f853e739df3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0260` | pylint | protected-access | temporary | inline | span | tests/agents/test_review_revised_fan_out.py | — | `sha256:88e385472cb38305cb394629a2b70db68bc9d3948e945402fb6d28c2205c0f3b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0261` | pylint | protected-access | temporary | inline | span | tests/server/test_run_gc_background.py | — | `sha256:92ebe88b2384f591fa6078933805e7aed62c4b6a1a7796553d9b96277b56b3e0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0262` | pylint | protected-access | temporary | inline | span | tests/server/test_stdio_progress.py | — | `sha256:4652118967857921e9ef15a9df15326042e1a41eb5154c538b8afc2e09b3b594` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0263` | pylint | too-many-arguments | temporary | inline | span | scripts/gauss_live_probe.py | — | `sha256:e291191c021b141db59904d57b1b1d7ade99c76f2f2f38f90128c4785a30db39` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0264` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/chat/service.py | — | `sha256:ad4d4dd0361f7f453d9ecdb6b0c08c35739ff0751ca885d54f7a30eacc1aa133` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0265` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/data/nl2sql.py | — | `sha256:84dc17c51613d59f92ae20603553449d971f33d170353c0623b7992dcc0c222c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0266` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/deep_genome/coordinator.py | — | `sha256:6d9bf21762df6cec5ca632c7fa0145f019ce0d56231f6e3cab1d6591eaca2e30` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0267` | pylint | too-many-arguments | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | DeepGenomeDispatchMixin | `sha256:b20197eb42c0782169622e60d20915ebe11b19b8d0759e2273072356ec65cdc6` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0268` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/knowledge/retrieval.py | — | `sha256:6bdcafe21a10f37d78813447e1288e33fcbda7783626b3fbaa0cffb4999a0e17` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0269` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:1a370abea4bb3e91e460ecacc19991cbcbb3c4d8647f618e0ce59ca0a61ca387` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0270` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:4bcb94ae89470bff34be0204f90a2751b032c2e1d75dceb846762909b5ce38c4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0271` | pylint | too-many-arguments | temporary | inline | span | src/mcp_server_phytomni/storage/uploads.py | — | `sha256:df677e7d6aeed70a2ecd9f25c93808d3e3f5ce8b83aa3ef2514b250d61e02a36` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0272` | pylint | too-many-instance-attributes | temporary | inline | span | e2e/helpers/polling.py | — | `sha256:ff614cad455dfea7ce48195584275f9faa4773dffacf4088e3341222c8bfc4b1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0273` | pylint | too-many-instance-attributes | temporary | inline | symbol | scripts/static_analysis/model.py | Exemption | `sha256:eeb04531fd760d523b2fd486a640c3ea24dabd91f85e0b47a52bcafdc31d251d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0274` | pylint | too-many-instance-attributes | temporary | inline | symbol | scripts/static_analysis/model.py | Finding | `sha256:661b324cf7d8671f2ab7caf8bee1839f24b77a0de25723c26767926e3a4ba4d8` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0275` | pylint | too-many-lines | temporary | inline | span | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | — | `sha256:309fe61bb354c9fc9d3020133970dcff287458c0dcad8a7c86224baae953716c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0276` | pylint | too-many-lines | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:a301ef88f14d3dd14fabdf89188852adf8ca15118b49e67eb1345231652dc7f0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0277` | pylint | too-many-lines | temporary | inline | span | src/mcp_server_phytomni/mcp/result_formatting.py | — | `sha256:70b34244fbe17b8196a2dc327969febd7f2be17c37563661b5bf523edf2c16c7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0278` | pylint | too-many-locals | temporary | inline | span | src/mcp_server_phytomni/agents/chat/service.py | — | `sha256:619fb3f18b0672ce5a813d4a2f3c75fc0e1a731df01e119a42c8705bfb1766a9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0279` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | DeepGenomeDispatchMixin | `sha256:f448c3daf964aa5c4402edbe600c046c6aa04060521ad616ade6e97f5aa403ce` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0280` | pylint | too-many-locals | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:d98c5d2f2fd683297caf6428dd7821e725b53234dc57162abb2e03b63f0d8b1a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0281` | pylint | too-many-locals | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:fca70926837df2569a4ae2b4c8be446ca58aefd1a8367b32c8d63f7f391d0589` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0282` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_resume_a2a_task | `sha256:b4a78c743023a86c2bafe52e9900deaea4beaf22c69ecc45afd8726fa7676c02` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0283` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_resume_a2ui_run | `sha256:09933681134a0fe09554c54288f03b7f7bffe7939439e79ae36d0b1d8821d20a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0284` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_resume_review_run | `sha256:4d087aa10fc4bc07a1db0099971c761aa153b3c68a4ba44adcb1af6783b176e9` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0285` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_chat_completion | `sha256:8564f40fa2157f740ee9f7bff75d93ae929acea50965d862fbdd951cee5c5ecd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0286` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_review_a2ui_pause | `sha256:1b5e7ee2821e52528a7c5b95ad412cbe7f2646d465c0b31dabe61ec0be04cfc5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0287` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/mcp/result_formatting.py | \_format_design_result | `sha256:8628b792cea0d61f8299df4d795e861c92b404813e5abe827b980f211edbe83f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0288` | pylint | too-many-locals | temporary | inline | span | src/mcp_server_phytomni/storage/uploads.py | — | `sha256:d713586935bbfb52b818a5e473e7d2169737526f18839e9196e5831051e4c466` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0289` | pylint | too-many-locals | temporary | inline | span | tests/agents/test_deep_genome_lifecycle.py | — | `sha256:4826e10b79938726acf6c101e54b772710da0d577ca3bb9190a6754b21af8660` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0290` | pylint | too-many-positional-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/data/nl2sql.py | — | `sha256:5070abd4d3f8904dfe8375cdec6872bd6d155615cc6bd4b54f914cf5e07c75d2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0291` | pylint | too-many-positional-arguments | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | DeepGenomeDispatchMixin | `sha256:0798e8ccfb5acef4a3251eb0b379d705d9bd8adf987f3f9f95c931665356ccbc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0292` | pylint | too-many-statements | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:d31ea0d7a171aebd3fbf1bd2c0eca47443f9cf55147649ba476c187710e62378` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0293` | pylint | wrong-import-position | temporary | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:637afabc246f45d9145f3aa93a39fe94d65fb830b2067196e8f33d8b77ed1847` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0294` | pymarkdown | md013 | temporary | config | config | pyproject.toml | tool.pymarkdown.plugins.md013.enabled | `sha256:34d8c475a236a211d36db927ee2de462f03fdb4f3f07864359540255c750bf01` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0295` | pytest | error | temporary | config | config | pyproject.toml | tool.pytest.ini_options.filterwarnings[0] | `sha256:0aa661937816a1fb17f333eb9a372e15f3093ed7bf8b75bc5d904d38422ef20f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0298` | pytest | ignore:ssl.PROTOCOL_TLS is deprecated:DeprecationWarning | temporary | config | config | pyproject.toml | tool.pytest.ini_options.filterwarnings[1] | `sha256:3b0438047d9767b1eb34f8a245bfa1e11abe24c3fc8011aee6a4f08905aef12a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0299` | ruff | ASYNC109 | temporary | config | config | pyproject.toml | tool.ruff.lint.ignore | `sha256:5993cb3b3995e8a9e9d1b75ec77d7be80c2deb72a69b7a164790e0d8fea7998a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0300` | ruff | ASYNC110 | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/report.py | \_write_async | `sha256:a7cb1e8424467f0727f7a3b99bf30356d9bb18b99462d892fbf92402f43dd198` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0301` | ruff | ASYNC110 | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_purge_expired_runs_best_effort_async | `sha256:a52705825d478feeafb241f23127685f24facd52ebf4d62d6ed335d1dd04782d` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0302` | ruff | ASYNC110 | temporary | inline | symbol | src/mcp_server_phytomni/api/relay/obs.py | \_wait_obs_future | `sha256:a8f3e3adef4dab97a6423e96bc1f91159c519a5060e52a58156302de97e99e33` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0303` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/test_analyst_storage.py | test_download_obs_out_via_relay_downloads_all_when_flagged.\_to_path | `sha256:1aee89a4c5356ddde0a8379b004fae29504f55dc9513381b1c9d6eee6c1b2f07` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0304` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/test_analyst_storage.py | test_download_obs_out_via_relay_writes_matching_objects.\_to_path | `sha256:ac5ec39c27751b8e7b283ac0090a0d1e803b0c96217c8d3590dd14f0853b8a0f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0305` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/test_storage_downloads.py | test_download_obs_file_uses_relay_in_relay_mode | `sha256:8d835ccc45ce6915b38f111bfa8689a1ceb6c93974bdae138da8792cc2bfe247` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0306` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/test_storage_downloads.py | test_download_obs_file_uses_relay_in_relay_mode.\_stream_to_path | `sha256:908ed0a856529543c54881f7a07925c43568e62fd1333946b7c906972a23232e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0307` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/utils/test_obs_uploads.py | test_download_list_convert_marks_sdk_downloads_for_cleanup.fake_download_with_retry | `sha256:2b865e3bde39ff43b92ec08f6acfc49e36c7ef5e89dce1d7baabec5566394545` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0308` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/utils/test_obs_uploads.py | test_download_obs_file_falls_back_to_sdk_temp_path | `sha256:b5f743db9e5cd682ab17e49ce05bb44bcb50fc55f875050b594657de797d92ee` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0309` | ruff | ASYNC240 | temporary | inline | symbol | tests/unit/utils/test_obs_uploads.py | test_download_obs_file_falls_back_to_sdk_temp_path.fake_download_with_retry | `sha256:f444a0e861c793085939705e45135485fdd22964728e9f98435bb58774153990` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0310` | ruff | E402 | temporary | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:5bb9d315b606c4e79f7f08a5a3896da1ae9fe6ea43c2d6d2055599a1c4e33f40` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0311` | ruff | E402 | temporary | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:9e583e7c25de4e2c0f6b97674641e1e427cb71f230c2aa96fb00b4aa48ddf6cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0312` | ruff | N802 | temporary | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/\*.pyi | `sha256:bf0326cb042985ec93478cbec49d2e724864485a45acf6ad1e15bc9d520eb9a4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0313` | ruff | N803 | temporary | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/*.pyi | `sha256:04fd5950474f7a9f1e297946d6fbe12928593497186dbe231583d79859f82d30` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0314` | ruff | N815 | temporary | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/\*\*/*.pyi | `sha256:c46f9c1d9fbddac831ac4f216f43c4ff6703ad4274821d10b4f0186703a912e0` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |

## Review fields

### `SAE-STR-0001`

Rationale:

```text
The profile mixin keeps two graph-bound BI/cache helpers on the consuming agent.
```

Counterfactual:

```text
Extracting free functions would re-thread shared configuration and BI/cache context and weaken the profile boundary.
```

Risk:

```text
A future helper added without graph-state coupling could be incorrectly retained in this structural mixin.
```

### `SAE-STR-0002`

Rationale:

```text
The report mixin groups graph nodes that share DeepGenome stores, mounted subgraphs, provider dispatch, and durable finalization.
```

Counterfactual:

```text
Extracting the thirteen stateful helpers would fragment the finalization barrier and enlarge the error-prone argument surface.
```

Risk:

```text
A future stateless report helper could be hidden by this class-level exception instead of being extracted.
```

### `SAE-STR-0003`

Rationale:

```text
The report mixin keeps critique, supplementary retrieval formatting, and citation auditing on the review agent.
```

Counterfactual:

```text
Extracting the helpers would re-thread retrieval, citation, and failure-accumulator state and split the report invariant.
```

Risk:

```text
A future helper with no review-agent state could be incorrectly retained in this structural mixin.
```

### `SAE-TMP-0001`

Rationale:

```text
[flake8].extend-ignore='E203,W503'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0002`

Rationale:

```text
[flake8].extend-ignore='E203,W503'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0006`

Rationale:

```text
tool.mypy.overrides[0].ignore_missing_imports=True
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0007`

Rationale:

```text
tool.mypy.overrides[1].ignore_missing_imports=True
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0009`

Rationale:

```text
type: ignore[misc]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0010`

Rationale:

```text
type: ignore[misc]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0011`

Rationale:

```text
type: ignore[prop-decorator]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0012`

Rationale:

```text
Similar lines in 2 files
==e2e.helpers.polling:[336:345]
==mcp_client_phytomni.http_client:[370:379]
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _public_progress(value: Any) -> Mapping[str, int | bool | str]:
    """Keep only the documented public progress keys and value types."""
    if not isinstance(value, Mapping):
        return {}
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0013`

Rationale:

```text
Similar lines in 2 files
==e2e.helpers.polling:[575:581]
==mcp_client_phytomni.http_client:[278:284]
    "intermediate_report",
    "final_report",
    "report_stage",
    "report_completeness",
    "report_revision",
    "report_updated_at",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0014`

Rationale:

```text
Similar lines in 2 files
==scripts.compare_gauss_queries:[294:301]
==scripts.gauss_live_probe:[239:246]
    parser.add_argument(
        "--environment-class",
        default=os.getenv("PHYTOMNI_ENVIRONMENT_CLASS", "unspecified"),
    )
    parser.add_argument(
        "--output",
        type=Path,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0015`

Rationale:

```text
Similar lines in 2 files
==scripts.compare_gauss_queries:[91:99]
==scripts.gauss_live_probe:[209:217]
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            cwd=Path(__file__).resolve().parents[1],
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0017`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.helpers:[17:25]
==scripts.static_analysis.model:[58:66]
    tool: str
    rule: str
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0018`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.helpers:[19:25]
==scripts.static_analysis.model:[80:86]
    mechanism: Mechanism
    target_kind: TargetKind
    path: str
    symbol: str | None
    peer_path: str | None
    peer_symbol: str | None
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0019`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.pylint:[341:349]
==scripts.static_analysis.collectors.reverse:[32:40]
        return subprocess.run(
            list(command),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0020`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.pylint:[342:348]
==scripts.static_analysis.collectors.reverse:[26:32]
                list(command),
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0021`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.pylint:[101:115]
==scripts.static_analysis.inventory:[161:174]
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CollectionError(
            f"git file inventory failed: {result.stderr.strip()}"
        )
    return tuple(root / line for line in result.stdout.splitlines() if line)


def tracked_python_files(root: Path) -> tuple[str, ...]:
    """Return tracked implementation Python paths in Git order."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0022`

Rationale:

```text
Similar lines in 2 files
==scripts.static_analysis.collectors.source:[84:98]
==scripts.static_analysis.fingerprints:[48:62]
        name = _definition_name(node)
        next_parents = parents
        next_depth = depth
        if name is not None:
            next_parents = (*parents, name)
            next_depth += 1
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and start <= line <= end
            ):
                matches.append(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0024`

Rationale:

```text
Similar lines in 2 files
==mcp_client_phytomni.http_client:[19:30]
==test_deep_genome_contract_docs:[65:76]
        "planning_complete",
        "brief_gene_status",
        "total",
        "planned",
        "submitted",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0025`

Rationale:

```text
Similar lines in 2 files
==mcp_client_phytomni.http_client:[19:32]
==mcp_server_phytomni.mcp.result_formatting:[38:50]
    "planning_complete",
    "brief_gene_status",
    "total",
    "planned",
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0026`

Rationale:

```text
Similar lines in 2 files
==mcp_client_phytomni.http_client:[22:32]
==mcp_server_phytomni.runtime.deep_genome_report_snapshot:[27:36]
    "planned",
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0027`

Rationale:

```text
Similar lines in 2 files
==mcp_client_phytomni.http_client:[278:288]
==test_deep_genome_contract_docs:[44:54]
    "intermediate_report",
    "final_report",
    "report_stage",
    "report_completeness",
    "report_revision",
    "report_updated_at",
    "progress",
    "degraded",
    "degraded_reason",
    "failures",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0028`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.__init__:[42:47]
==mcp_server_phytomni.agents.analyst.agent:[41:46]
    "ANALYST_CONFIG_FIELD_MAP",
    "ANALYST_SECRET_FIELD_MAP",
    "ANALYST_SENSITIVE_FIELD_MAP",
    "AnalystAgent",
    "AnalystAgentsState",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0029`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.agent:[39:44]
==mcp_server_phytomni.agents.analyst.defaults:[53:58]
__all__ = [
    "ANALYST_CONFIG",
    "ANALYST_CONFIG_FIELD_MAP",
    "ANALYST_SECRET_FIELD_MAP",
    "ANALYST_SENSITIVE_FIELD_MAP",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0030`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.core:[107:113]
==mcp_server_phytomni.agents.brief_gene.core:[185:191]
        self._knowledge_app: CompiledStateGraph[
            KnowledgeState,
            MemoryGraphContext,
            KnowledgeInput,
            KnowledgeOutput,
        ] = build_knowledge_app(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0031`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.core:[178:187]
==mcp_server_phytomni.agents.data.agent:[304:313]
        workflow.add_node(
            "knowledge",
            make_knowledge_node_wrapper(
                knowledge_app=knowledge_app,
                build_input_fn=lambda state: state["knowledge_payload"],
                extract_output_fn=lambda ko: ko,
                response_key="knowledge_response",
            ),
        )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0032`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.core:[234:244]
==mcp_server_phytomni.agents.brief_gene.core:[266:277]
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0033`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.core:[457:463]
==mcp_server_phytomni.agents.analyst.submission:[123:129]
        is_create_dir = kwargs.get("is_create_dir", ANALYST_CONFIG.CREATE_DIR)
        output_dir = kwargs.get("output_dir", ANALYST_CONFIG.OUTPUT_DIR)
        compute_resource = kwargs.get(
            "compute_resource",
            ANALYST_CONFIG.COMPUTE_RESOURCE,
        )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0034`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.core:[500:509]
==mcp_server_phytomni.agents.analyst.submission:[76:88]
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates={
            "USER_ID": user_id,
            "CREATE_DIR": is_create_dir,
            "OUTPUT_DIR": output_dir,
            "COMPUTE_RESOURCE": compute_resource,
        },
    )


def _sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0035`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.graph_chat_subgraph:[112:122]
==mcp_server_phytomni.agents.review.planning:[164:174]
        phyto_response = state.get("chat_response") or {}
        content = "{}"
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0036`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.graph_chat_subgraph:[114:121]
==mcp_server_phytomni.agents.review.summary:[203:210]
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0037`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.graph_chat_subgraph:[114:122]
==mcp_server_phytomni.agents.review.summary:[111:119]
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0038`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.graph_chat_subgraph:[373:381]
==mcp_server_phytomni.agents.review.planning:[166:174]
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0039`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.graph_chat_subgraph:[555:583]
==mcp_server_phytomni.agents.review.planning:[142:174]
        }

    async def tool_extract_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the tool-extraction chat response into the legacy delta.

        Mirrors the response-parsing half of ``tool_extract_node`` but
        reads the chat response from ``state['chat_response']``
        instead of awaiting a fresh ``phyto_chat`` call.

        Args:
            state: The current workflow state. Reads the upstream
                ``chat_response`` written by the shared chat node.

        Returns:
            A state delta with the parsed ``extracted_tools`` list.
        """
        phyto_response = state.get("chat_response") or {}
        content = "{}"
        if (
            phyto_response
            and phyto_response.get("choices")
            and len(phyto_response["choices"]) > 0
            and phyto_response["choices"][0].get("message")
            and phyto_response["choices"][0]["message"].get("content")
        ):
            content = phyto_response["choices"][0]["message"]["content"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0040`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.planning:[101:107]
==mcp_server_phytomni.agents.analyst.submission:[178:184]
        **_shared_arun_kwargs(
            goal_description=goal_description,
            output_dir=output_dir,
            compute_resource=compute_resource,
            data_list=data_list,
        ),
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0041`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.analyst.state:[114:119]
==mcp_server_phytomni.agents.data.state:[70:77]
    chat_payload: dict[str, Any] | None
    chat_response: dict[str, Any] | None
    pending_post_knowledge: str | None
    knowledge_payload: dict[str, Any] | None
    knowledge_response: dict[str, Any] | None
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0042`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.core:[119:124]
==tests.agents.test_deep_genome_brief_gene_mount:[244:249]
        "go_string": "",
        "kegg_string": "",
        "interpro_string": "",
        "description_string": "",
        "retrieved_docs": [],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0043`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.core:[119:130]
==tests.agents.test_brief_gene_knowledge_subgraph:[115:123]
        "go_string": "",
        "kegg_string": "",
        "interpro_string": "",
        "description_string": "",
        "retrieved_docs": [],
        "retrieve_context": "",
        "follow_up_questions": [],
        "final_response": {},
        # X3b A architecture preamble fan-out fields.
        # All seeded empty here so the TypedDict contract holds at
        # ``arun`` entry; nodes populate them during the workflow.
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0044`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.core:[131:138]
==mcp_server_phytomni.agents.brief_gene.homology:[51:58]
        "orthologs_data": {"gene_list": []},
        "paralogs_data": {"gene_list": []},
        "interaction_data": {"gene_list": []},
        "ortholog_count": 0,
        "ortholog_species_count": 0,
        "paralog_count": 0,
        "interaction_count": 0,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0045`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.core:[264:277]
==mcp_server_phytomni.agents.knowledge.agent:[159:171]
        workflow.add_node("follow_up_prep_node", self.follow_up_prep_node)
        workflow.add_node("follow_up_post_node", self.follow_up_post_node)
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0046`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.graph_knowledge_subgraph:[81:87]
==mcp_server_phytomni.agents.review.agent:[259:265]
        knowledge_app = self._knowledge_app
        if knowledge_app is None:
            raise RuntimeError(
                "unreachable: _knowledge_app must be built in __init__"
            )
        workflow.add_node(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0047`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.graph_knowledge_subgraph:[88:95]
==mcp_server_phytomni.agents.review.agent:[266:273]
        )
        workflow.add_node(
            "retrieve_worker_node",
            self.make_retrieve_worker_node(knowledge_app),
        )
        workflow.add_node("retrieve_reduce_node", self.retrieve_reduce_node)
        workflow.add_conditional_edges(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0048`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.resolve_query:[172:182]
==mcp_server_phytomni.agents.network.resolve_query:[182:192]
        sensitive_config,
    )
    chat_kwargs["response_format"] = _RESOLVER_JSON_SCHEMA

    try:
        phyto_response = await asyncio.wait_for(
            phyto_chat(user_query=rendered_user_query, **chat_kwargs),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0049`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.resolve_query:[234:262]
==mcp_server_phytomni.agents.network.resolve_query:[287:320]
    choices = phyto_response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None


def _normalize_candidates(
    top_to_id: Any,
    candidates_field: Any,
    valid_to_ids: set,
    top_species_code: str,
) -> list[GeneNetworkToIdCandidate]:
    """Build candidate list, dropping blanks and ids outside the catalog.

    Validation against ``valid_to_ids`` is the resolver's last line
    of defense against LLM hallucination: the system prompt instructs
    the LLM to pick from the supplied catalog, but a non-compliant
    completion still loses its bogus ids here rather than reaching
    the downstream BI / agent layer that has no equivalent check.

    Per-candidate ``species_code`` defaults to ``top_species_code``
    when absent or blank so downstream consumers always see a
    populated three-letter code.
    """
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0050`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.resolve_query:[269:284]
==mcp_server_phytomni.agents.network.resolve_query:[327:342]
                continue
            confidence_raw = raw.get("confidence", 0.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            candidate_species_raw = raw.get("species_code")
            candidate_species = (
                candidate_species_raw.strip()
                if isinstance(candidate_species_raw, str)
                else ""
            )
            species_code = candidate_species or top_species_code
            out.append(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0051`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.resolve_query:[93:100]
==mcp_server_phytomni.agents.network.resolve_query:[97:104]
            "species_code": {"type": "string"},
            "confidence": {
                "type": "number",
                "minimum": props["confidence"]["minimum"],
                "maximum": props["confidence"]["maximum"],
            },
        },
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0052`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.state:[106:111]
==tests.agents.test_deep_genome_brief_gene_mount:[38:43]
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
    retrieved_docs: list[dict[str, Any]]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0053`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.state:[130:143]
==tests.agents.test_deep_genome_brief_gene_mount:[51:71]
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    introduction_report: str


def _build_fake_brief_gene_app(
    output: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for BriefGeneAgent.

    ``find_subgraph_pregel`` recognises ``CompiledStateGraph`` by
    isinstance, so a SimpleNamespace cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` keeps the test
    fully offline while still presenting a real compiled subgraph
    for the mount factory closure to hold. The optional ``output``
    map stages the canned BriefGeneOutput keys the stub returns.
    """

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0054`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.state:[61:67]
==tests.agents.test_deep_genome_brief_gene_mount:[36:42]
    gene_id: str
    species_code: str
    go_string: str
    kegg_string: str
    interpro_string: str
    description_string: str
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0055`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.brief_gene.state:[67:76]
==tests.agents.test_deep_genome_brief_gene_mount:[47:71]
    gene_structure_string: str
    orthologs_data: dict[str, Any]
    paralogs_data: dict[str, Any]
    interaction_data: dict[str, Any]
    section1_markdown: str
    section2_markdown: str
    section3_markdown: str
    section4_markdown: str
    introduction_report: str
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0056`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.chat.a2ui_graph:[197:207]
==test_a2ui_actions_http:[169:179]
        "response": {
            "choices": [
                {
                    "message": {
                        "content": _CANCEL_MESSAGE,
                        "follow_up_questions": [],
                    }
                }
            ]
        },
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0057`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.data.agent:[220:230]
==mcp_server_phytomni.agents.review.agent:[147:157]
        self._knowledge_app: (
            CompiledStateGraph[
                KnowledgeState,
                MemoryGraphContext,
                KnowledgeInput,
                KnowledgeOutput,
            ]
            | None
        )
        self._knowledge_app = build_knowledge_app(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0058`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.data.agent:[261:271]
==mcp_server_phytomni.agents.review.agent:[247:259]
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0059`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.data.agent:[261:274]
==mcp_server_phytomni.agents.knowledge.agent:[161:174]
        workflow.add_node(
            "chat",
            make_chat_node_wrapper(
                build_input_fn=lambda state: state["chat_payload"],
                extract_output_fn=lambda chat_output: (
                    chat_output.get("response") or {}
                ),
                response_key="chat_response",
            ),
        )
        workflow.add_conditional_edges(
            START,
            make_async_router(self.route_start),
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0060`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.deep_genome.agent:[89:109]
==mcp_server_phytomni.runtime.deep_genome_store:[70:82]
    {
        "brief gene profile failed",
        "final synthesis failed",
        "final report unavailable",
        "final report publication failed",
        "no usable analysis result",
        "workflow interrupted by service restart",
        "local coordinator failed to start",
        "submission tracking failed",
        "remote analysis tracking failed",
    }
)


def _finalization_failure_reason(
    exc: BaseException | None,
    *,
    cancelled: bool = False,
) -> str:
    """Map one workflow exception to fixed, local terminal wording."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0061`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.deep_genome.dispatch:[172:181]
==test_deep_genome_work_items:[72:81]
        "gene_expression_tissues",
        "gene_expression_cultivars",
        "gene_expression_treatments",
        "gene_expression_genotypes",
        "single_cell_analysis",
        "promoter_analysis",
        "smep_analysis",
        "smoc_analysis",
        "protein_structure_analysis",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0062`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[373:380]
==mcp_server_phytomni.agents.research.agent:[428:435]
                dependencies=dependencies,
            )
            if result is not None:
                if result["status"] == "input_required":
                    task_id = result.get("task_id")
                    if not task_id:
                        raise RuntimeError(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0063`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[501:513]
==mcp_server_phytomni.agents.research.agent:[630:648]
                    **interop_state_update(
                        make_interop_record(
                            target_id=pending["target_id"],
                            kind="a2a",
                            capability=pending["capability"],
                            status="input_required",
                            latency_seconds=perf_counter() - started,
                        )
                    ),
                },
            )

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0064`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[553:562]
==mcp_server_phytomni.agents.research.agent:[677:686]
                            "degraded",
                            perf_counter() - started,
                            True,
                        )
                    )
                )
            else:
                updates.update(
                    interop_evidence_update(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0065`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[560:574]
==mcp_server_phytomni.agents.research.agent:[770:798]
        updates.update(
            interop_evidence_update(
                evidence,
                status="completed",
                latency_seconds=perf_counter() - started,
            )
        )
        return updates

    async def arun(
        self,
        paper_text: str,
        data_list: dict[str, str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Conduct in silico research and return task_ids.

        Args:
            paper_text: Scientific paper text to analyze.
            data_list: Dictionary of data sources for research.
            user_id: Optional user identifier.
            obs_file_list: List of OBS files to include as context.
            output_dir: Optional output directory path.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids mapping research goals to task IDs.
        """
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0066`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[563:579]
==mcp_server_phytomni.agents.research.agent:[687:703]
                        status="completed",
                        latency_seconds=perf_counter() - started,
                    )
                )
        return updates

    async def resume_research_a2a(
        self,
        state: InSilicoResearchState,
    ) -> dict[str, Any]:
        """Resume pending external A2A work after a graph interrupt."""
        pending_items = state.get("a2a_pending", [])
        if not pending_items:
            return {}
        pending = pending_items[0]
        started = perf_counter()
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0067`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.design.agent:[659:689]
==mcp_server_phytomni.agents.research.agent:[770:801]
        updates.update(
            interop_evidence_update(
                evidence,
                status="completed",
                latency_seconds=perf_counter() - started,
            )
        )
        return updates

    async def arun(
        self,
        paper_text: str,
        data_list: dict[str, str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Conduct in silico research and return task_ids.

        Args:
            paper_text: Scientific paper text to analyze.
            data_list: Dictionary of data sources for research.
            user_id: Optional user identifier.
            obs_file_list: List of OBS files to include as context.
            output_dir: Optional output directory path.
            thread_id: Optional thread ID for checkpointer.

        Returns:
            Dict with task_ids mapping research goals to task IDs.
        """
        return await run_analysis_graph(
            self.app,
            {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0068`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.environment.agent:[89:96]
==mcp_server_phytomni.agents.evolution.agent:[149:156]
    chat_output = await _cached_chat_app().ainvoke(
        build_chat_input(user_query=prompt, chat_kwargs=chat_kwargs_bag)
    )
    phyto_response = extract_chat_response(chat_output)
    if not phyto_response:
        return None
    content = phyto_response["choices"][0]["message"]["content"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0069`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.environment.graph:[171:178]
==mcp_server_phytomni.agents.evolution.graph:[178:185]
        "output_dir": inputs.output_dir,
        "prompt_parts": (
            inputs.goal_description,
            inputs.meta,
            inputs.data_list,
        ),
        "compute_resource": submit_kwargs.get(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0070`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.research.interop:[238:245]
==mcp_server_phytomni.api.app:[2652:2659]
        cache = caches.get(target.id)
        if cache is None:
            cache = DiscoveryCache(
                ttl_seconds=target.discovery_ttl_seconds,
                max_entries=ApiConfig().INTEROP_CACHE_MAX_ENTRIES,
            )
            caches[target.id] = cache
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0071`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.agents.shared.chat_subgraph:[93:99]
==mcp_server_phytomni.agents.shared.knowledge_subgraph:[107:113]
        pending = state.get(pending_post_key)
        if isinstance(pending, str) and pending:
            return pending
        if default is not None:
            return default
        raise ValueError(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0072`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.api.auth:[258:285]
==mcp_server_phytomni.api.relay.audit:[190:207]
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    def create(
        self,
        user_id: str,
        name: str | None = None,
        expires_at: datetime | None = None,
        scopes: Sequence[str] | None = None,
    ) -> CreatedApiKey:
        """Mint and persist a new key, returning the one-time plaintext.

        Args:
            user_id: The user the key authenticates.
            name: Optional human label.
            expires_at: Optional aware datetime after which the key fails.
            scopes: Optional granted scopes; None or empty mints an
                all-access key for backward compatibility.

        Returns:
            The created key; ``api_key`` is the only time the plaintext
            is available.
        """
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0073`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.api.relay.__init__:[20:25]
==mcp_server_phytomni.api.relay.audit:[27:32]
__all__ = [
    "RelayAuditRecord",
    "RelayAuditQuery",
    "RelayAuditStore",
    "get_audit_store",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0074`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.api.relay.audit_filter:[27:38]
==test_relay_audit_filter:[30:40]
    {
        "authorization",
        "x-api-key",
        "x-service-token",
        "x-auth-token",
        "token",
        "cookie",
        "set-cookie",
    }
)

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0075`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.config.required_env:[16:28]
==test_required_env:[73:88]
        "TOKEN_URL",
        "RETRIEVE_URL",
        "RERANK_URL",
        "DATABASE_URL",
        "ANALYSIS_URL",
        "REPO_ID",
        "REPO_ID_DICT",
        "WORKSPACE_ID",
        "SUBJECT_ID",
        "OBS_SERVER",
    )


def test_missing_environment_is_sorted_and_value_safe() -> None:
    """Only missing names are returned, in deterministic order."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0076`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.config.required_env:[41:46]
==test_required_env:[35:40]
    "DATA_REPO_ID",
    "TOOL_REPO_ID",
    "PROTOCOL_REPO_ID",
    "SPA_REPO_ID",
    "APP_ID",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0077`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.graphs.analyst_to_knowledge_adapters:[51:78]
==mcp_server_phytomni.graphs.review_to_knowledge_adapters:[51:80]
        "repo_id_dict": dict(repo_id_dict),
        "is_generate": False,
        "is_follow_up": False,
    }


def extract_review_knowledge_response(
    knowledge_output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project ``KnowledgeOutput.retrieved_docs`` into the doc list.

    The review drafting step reads the raw doc list and runs its own
    fragment formatting + token-budget truncation (via
    ``_dimension_fragments``); the knowledge subgraph stores the docs
    under ``KnowledgeOutput.retrieved_docs``. This helper unwraps the
    list and defaults to ``[]`` when the upstream returned no docs so
    the downstream fragment loop still iterates over a list rather
    than ``None``.

    Args:
        knowledge_output: The knowledge subgraph's final state mapping
            (``KnowledgeOutput``-shaped).

    Returns:
        The raw retrieved-doc list, or ``[]`` if the upstream returned
        ``None`` or omitted the key.
    """
    docs = knowledge_output.get("retrieved_docs")
    return list(docs) if docs is not None else []
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0078`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.mcp.app:[776:781]
==mcp_server_phytomni.mcp.handlers:[109:114]
        user_query=args.user_query,
        obs_file_list=args.obs_file_list,
        server_dir=scratch_server_dir(chat_config, "chat"),
        **chat_kwargs(chat_config, runtime.sensitive),
        **obs_kwargs(chat_config, runtime.obs_credentials),
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0079`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.mcp.result_formatting:[1168:1175]
==test_result_formatting_projection:[222:229]
        "id",
        "object",
        "created",
        "model",
        "choices",
        "usage",
        "formatted",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0080`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.mcp.result_formatting:[38:49]
==test_task_reconcile:[160:171]
        "planning_complete",
        "brief_gene_status",
        "total",
        "planned",
        "submitted",
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0081`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.mcp.result_formatting:[922:932]
==mcp_server_phytomni.runtime.deep_genome_store:[201:207]
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else 0
    )


def _deep_genome_progress(value: Any) -> dict[str, Any]:
    """Copy only the public DeepGenome progress counters."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0082`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.runtime.deep_genome_report_snapshot:[27:35]
==test_deep_genome_contract_docs:[68:76]
    "planned",
    "submitted",
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0083`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.runtime.memory.__init__:[45:52]
==mcp_server_phytomni.runtime.memory.models:[385:392]
__all__ = [
    "DEFAULT_MEMORY_MAX_CONTENT_BYTES",
    "DEFAULT_MEMORY_MAX_ITEMS",
    "DEFAULT_MEMORY_MAX_RETRIEVAL",
    "DEFAULT_MEMORY_MAX_TAG_BYTES",
    "DEFAULT_MEMORY_MAX_TAGS",
    "DEFAULT_MEMORY_MAX_TOTAL_BYTES",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0084`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.runtime.memory.__init__:[59:64]
==mcp_server_phytomni.runtime.memory.models:[394:399]
    "MemoryId",
    "MemoryItem",
    "MemoryKind",
    "MemoryPolicy",
    "MemoryPolicyError",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0085`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.runtime.memory.__init__:[73:79]
==mcp_server_phytomni.runtime.memory.accessor:[38:45]
    "current_memory_accessor",
    "get_default_memory_accessor",
    "memory_accessor_context",
    "memory_policy_from_config",
    "resolve_memory_accessor",
]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0086`

Rationale:

```text
Similar lines in 2 files
==mcp_server_phytomni.runtime.memory.migrations:[23:31]
==test_memory_migrations:[26:34]
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        content TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0087`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_analyst_graph_io:[31:59]
==tests.agents.test_analyst_knowledge_subgraph:[27:58]
pytestmark = pytest.mark.agent

_CORE_MODULE = "mcp_server_phytomni.agents.analyst.core"


class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    retrieved_docs: list[dict[str, Any]]


def _build_fake_knowledge_app() -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for the KA subgraph.

    ``find_subgraph_pregel`` (the walker behind ``get_graph(xray=True)``)
    recognises ``CompiledStateGraph`` instances by isinstance, not by
    duck typing, so a ``SimpleNamespace`` cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` that returns an empty
    ``retrieved_docs`` list keeps the test fully offline while still
    presenting a real compiled subgraph for the wrapper's closure to
    capture.
    """

    async def _noop(state: _FakeKnowledgeState) -> dict[str, Any]:
        del state
        return {"retrieved_docs": []}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("noop", _noop)
    workflow.add_edge(START, "noop")
    workflow.add_edge("noop", END)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0088`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_analyst_graph_io:[36:59]
==tests.agents.test_data_chat_subgraph:[40:66]
class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    retrieved_docs: list[dict[str, Any]]


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` to return a deterministic compiled stub.

    ``DataAgent.__init__`` builds the per-instance compiled KA
    subgraph unconditionally; this chat-focused test substitutes a
    tiny compiled subgraph so construction stays offline (no real
    KnowledgeAgent compile, no real retrieve) while the chat-subgraph
    mount under test is exercised through the same graph.
    """

    async def _noop(state: _FakeKnowledgeState) -> dict[str, Any]:
        del state
        return {"retrieved_docs": []}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("noop", _noop)
    workflow.add_edge(START, "noop")
    workflow.add_edge("noop", END)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0089`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_analyst_knowledge_subgraph:[196:226]
==tests.agents.test_data_knowledge_subgraph:[198:228]
        sensitive_config=SensitiveConfig.load(),
    )
    assert agent._knowledge_app is fake_app


# ---------------------------------------------------------------------------
# Structural: the method_retrieve site mounts the prep+post pair.
# ---------------------------------------------------------------------------


def test_compiled_graph_xray_expands_knowledge_subgraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compiled graph exposes the shared knowledge subgraph to ``xray``.

    Structural check: ``StateGraph.get_graph(xray=True)`` walks the
    compiled graph and inlines any node whose body closes over a
    ``CompiledStateGraph``. Mounting knowledge via
    ``make_knowledge_node_wrapper(knowledge_app=...)`` keeps the
    compiled subgraph at the wrapper's closure free-vars, so
    ``find_subgraph_pregel`` discovers it and the xray render carries
    node keys prefixed with ``knowledge:``. A flat ``knowledge`` key
    with no child prefix would mean the wrapper hid the subgraph and
    the render reverted to an opaque box.
    """
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0090`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_analyst_knowledge_subgraph:[240:247]
==tests.agents.test_data_knowledge_subgraph:[246:253]
    agent = _build_agent(monkeypatch)
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(key.startswith("chat:") for key in node_keys), sorted(node_keys)
    assert any(key.startswith("knowledge:") for key in node_keys), sorted(
        node_keys
    )
    # Cross-product still substitutes the method_retrieve site.
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0091`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_analyst_knowledge_subgraph:[32:74]
==tests.agents.test_data_knowledge_subgraph:[31:73]
class _FakeKnowledgeState(TypedDict, total=False):
    """Minimal state shape for the offline knowledge-subgraph stub."""

    retrieved_docs: list[dict[str, Any]]


def _build_fake_knowledge_app() -> CompiledStateGraph:
    """Compile a one-node ``StateGraph`` to stand in for the KA subgraph.

    ``find_subgraph_pregel`` (the walker behind ``get_graph(xray=True)``)
    recognises ``CompiledStateGraph`` instances by isinstance, not by
    duck typing, so a ``SimpleNamespace`` cannot satisfy the xray
    expansion. Compiling a trivial ``StateGraph`` that returns an empty
    ``retrieved_docs`` list keeps the test fully offline while still
    presenting a real compiled subgraph for the wrapper's closure to
    capture.
    """

    async def _noop(state: _FakeKnowledgeState) -> dict[str, Any]:
        del state
        return {"retrieved_docs": []}

    workflow: StateGraph = StateGraph(_FakeKnowledgeState)
    workflow.add_node("noop", _noop)
    workflow.add_edge(START, "noop")
    workflow.add_edge("noop", END)
    return workflow.compile()


def _install_fake_knowledge_app(
    monkeypatch: pytest.MonkeyPatch,
) -> CompiledStateGraph:
    """Patch ``build_knowledge_app`` to return a deterministic compiled stub.

    ``DataAgent.__init__`` constructs the per-instance compiled KA
    subgraph unconditionally; tests substitute a tiny compiled
    subgraph so the structural xray walk discovers it through the
    wrapper's closure free-vars while keeping the test fully offline
    (no real KnowledgeAgent compile, no real retrieve).
    """
    fake_app = _build_fake_knowledge_app()
    monkeypatch.setattr(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0092`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_brief_gene_chat_subgraph:[48:56]
==tests.agents.test_brief_gene_knowledge_subgraph:[100:108]
            "is_follow_up": False,
            "gene_found": True,
            "gene_id": "AT1G01010",
            "query_id_version": "tair10",
            "gene_id_version": "tair10",
            "species_code": "ath",
            "species_latin_name": "Arabidopsis thaliana",
            "species_english_name": "thale cress",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0093`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_brief_gene_knowledge_subgraph:[115:120]
==tests.agents.test_deep_genome_brief_gene_mount:[349:354]
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "description_string": "",
            "retrieved_docs": [],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0094`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_chat_adapters:[41:79]
==tests.agents.test_review_follow_up_routing:[50:88]
    return SimpleNamespace(
        PROMPT_FILE="prompt.yaml",
        PROMPT_PATH="/tmp/prompts",
        FREQUENCY_PENALTY=0.0,
        N=1,
        PRESENCE_PENALTY=0.0,
        REASONING_EFFORT="medium",
        RESPONSE_FORMAT={"type": "text"},
        STREAM=False,
        TEMPERATURE=0.2,
        TOP_P=0.9,
        USER="consumer-user",
        TIMEOUT=120.0,
        RETRIABLE_CODES=[429, 500, 502, 503, 504],
        MAX_RETRIES=3,
    )


def _fake_sensitive_config() -> SimpleNamespace:
    """Build a SimpleNamespace stand-in for ``SensitiveConfig``.

    ``API_KEY`` exposes ``get_secret_value`` to mirror the real
    Pydantic ``SecretStr`` surface every consumer agent reads.
    """
    return SimpleNamespace(
        API_KEY=SimpleNamespace(get_secret_value=lambda: "sk-test"),
        BASE_URL="https://llm.example/v1",
        MODEL_ID="phyto-llm-v1",
    )


def test_build_chat_kwargs_for_packs_all_17_fields() -> None:
    """The returned dict has all 17 keys with the expected values.

    Pins the shared 17-key bag the data / knowledge / analyst chat
    sites pass. If the dispatch site ever adds or drops a kwarg, this
    test fails first so the adapter and the call sites stay aligned.
    """
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0095`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_data_chat_subgraph:[67:95]
==tests.agents.test_data_knowledge_subgraph:[72:115]
    monkeypatch.setattr(
        f"{_DATA_MODULE}.build_knowledge_app", lambda **_kwargs: fake_app
    )
    return fake_app


def _build_agent(monkeypatch: pytest.MonkeyPatch) -> DataAgent:
    """Construct a ``DataAgent`` with the chat subgraph mounted.

    The knowledge subgraph is always mounted at the retrieve site, so
    the helper installs an offline knowledge-app stub via
    :func:`_install_fake_knowledge_app` to keep construction offline,
    then isolates the chat-subgraph mount under test.
    """
    _install_fake_knowledge_app(monkeypatch)
    return DataAgent(
        data_config=DataConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _minimal_rewrite_state() -> DataAgentState:
    """Return the minimal state the rewrite prep node reads.

    The rewrite prep node only reads ``retrieve_prompt`` (the prompt
    already stitched by ``retrieve_post_node``); no other keys are
    read at this node, so the dict stays minimal.
    """
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0096`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_design_mount:[506:515]
==tests.agents.test_graphs_manifest_export:[363:372]
        "gene_expression_tissues_node",
        "gene_expression_cultivars_node",
        "gene_expression_treatments_node",
        "gene_expression_genotypes_node",
        "single_cell_node",
        "promoter_node",
        "smep_node",
        "smoc_node",
        "protein_structure_node",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0097`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_design_mount:[50:76]
==tests.agents.test_deep_genome_evolution_mount:[46:71]
        return f"{output_path}/results"

    async def _poll_remote_submission(
        submission: RemoteSubmission,
        context: Any,
        run_identity: Any,
        **_kwargs: Any,
    ) -> tuple[WorkItemOutcome, str]:
        results_dir = await _download_analysis_result(
            context,
            submission.output_dir,
            run_identity,
        )
        return (
            WorkItemOutcome("succeeded", "# usable result", None),
            results_dir,
        )

    def _generate_sub_summary(
        *,
        analysis_type: str,
        gene_id: str,
        state: Any,
        results_dir: Any = None,
    ) -> dict:
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0098`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_design_mount:[528:538]
==tests.agents.test_deep_genome_evolution_mount:[179:189]
        cast(CompiledStateGraph, app), _finalize
    )
    payload: Any = {
        "species_code": "osa",
        "target_gene": "g1",
        "task_index": 2,
    }
    out = await node(payload)

    assert out["analysis_completed_branches"] == 1
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0099`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_design_mount:[80:85]
==tests.agents.test_deep_genome_evolution_mount:[75:80]
        deep_genome_config=SimpleNamespace(USER_ID="u"),
        _raise_if_agent_failed=_raise_if_agent_failed,
        _download_analysis_result=_download_analysis_result,
        _poll_remote_submission=_poll_remote_submission,
        _generate_sub_summary=_generate_sub_summary,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0100`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_dispatch_routing:[205:215]
==tests.agents.test_deep_genome_report:[232:242]
            {
                "work_item_key": "evolution_analysis",
                "analysis_type": "evolution_analysis",
            },
            {
                "work_item_key": "promoter_design",
                "analysis_type": "promoter_design_analysis",
                "section_key": "digital_design",
            },
        ],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0101`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_dispatch_routing:[232:242]
==tests.agents.test_deep_genome_report:[264:274]
            {
                "work_item_key": "evolution_analysis",
                "analysis_type": "evolution_analysis",
            },
            {
                "work_item_key": "promoter_design",
                "analysis_type": "promoter_design_analysis",
                "section_key": "digital_design",
            },
        ],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0102`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_deep_genome_dispatch_routing:[243:252]
==tests.agents.test_deep_genome_report:[243:252]
            "task_0:evolution_analysis": {
                "analysis_type": "evolution_analysis",
                "status": "failed",
            },
            "task_10": {
                "analysis_type": "digital_design",
                "status": "failed",
            },
        },
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0103`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_design_promoter_design_for_gene:[25:41]
==tests.agents.test_design_protein_structure_for_gene:[25:41]
pytestmark = pytest.mark.agent


def _install_stub_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    submit_return: dict[str, Any] | None = None,
) -> AsyncMock:
    """Patch prompt / data / AnalystAgent / submit deps on design.agent."""
    monkeypatch.setattr(
        design_agent,
        "get_prompt",
        lambda *_a, **_kw: "prompt-stub",
    )
    monkeypatch.setattr(
        design_agent,
        "get_data_list",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0104`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_design_promoter_design_for_gene:[42:51]
==tests.agents.test_design_protein_structure_for_gene:[42:51]
    )
    monkeypatch.setattr(
        design_agent,
        "AnalystAgent",
        lambda **_kw: "analyst-agent-stub",
    )
    submit_mock = AsyncMock(
        return_value=submit_return
        or {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0105`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_design_promoter_design_for_gene:[53:68]
==tests.agents.test_design_protein_structure_for_gene:[53:68]
            "task_status": "SUCCEEDED",
        }
    )
    monkeypatch.setattr(
        design_agent, "submit_analyst_via_subgraph", submit_mock
    )
    return submit_mock


async def test_returns_submit_helper_result_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper returns the analyst-subgraph helper's projected dict."""
    submit_mock = _install_stub_dependencies(monkeypatch)

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0106`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_design_promoter_design_for_gene:[92:99]
==tests.agents.test_design_protein_structure_for_gene:[92:99]
        species_code="ath",
        gene_id="AT1G01010",
    )

    call_args = submit_mock.await_args
    assert call_args is not None
    request = call_args.args[3]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0107`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_environment_agent:[168:176]
==tests.agents.test_environment_analyst_subgraph:[81:93]
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    monkeypatch.setattr(
        environment_graph,
        "_build_submit_agent",
        lambda *_a, **_kw: ("analyst-agent-stub", "", "small", "thread-x"),
    )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0108`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_graphs_manifest_export:[178:193]
==tests.agents.test_visualize_agent_graphs_cli:[103:118]
        "query_judge_node",
        "fetch_annotation_node",
        "fetch_homology_interactions_node",
        "retrieve_prep_tasks_node",
        "retrieve_worker_node",
        "retrieve_reduce_node",
        "section_discovery_node",
        "section_cloning_node",
        "section_functional_node",
        "section_application_node",
        "introduction_node",
        "render_node",
        "follow_up_prep_node",
        "follow_up_post_node",
        "chat",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0109`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_resume_kernel:[25:42]
==tests.agents.test_resume_restart:[24:41]
    value: str
    decision: NotRequired[dict[str, Any]]
    final: NotRequired[str]


def _build_restart_app(checkpointer: AsyncSqliteSaver) -> Any:
    """Build a graph that pauses once, then finalizes on approval."""

    async def gate(state: _RestartState) -> dict[str, Any]:
        decision = interrupt({"draft": state["value"]})
        return {"decision": decision}

    async def finalize(state: _RestartState) -> dict[str, str]:
        decision = state.get("decision", {})
        approved = decision.get("approved")
        return {"final": "ok" if approved else "redo"}

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0110`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_resume_kernel:[43:48]
==tests.agents.test_resume_restart:[42:47]
    graph.add_node("gate", gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "gate")
    graph.add_edge("gate", "finalize")
    graph.add_edge("finalize", END)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0111`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_chat_single_shot:[156:164]
==tests.agents.test_review_follow_up_routing:[126:134]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "A review of photosynthesis.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0112`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_chat_single_shot:[23:45]
==tests.agents.test_review_revised_fan_out:[29:51]
pytestmark = pytest.mark.agent


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for the revised fan-out."""
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


# ---------------------------------------------------------------------------
# Flag-on prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_revised_prepare_tasks_node_returns_empty_delta() -> None:
    """``revised_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0113`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_chat_single_shot:[31:51]
==tests.agents.test_review_follow_up_routing:[78:97]
    )


# ---------------------------------------------------------------------------
# Per-site prep-node assertions.
# ---------------------------------------------------------------------------


async def test_plan_query_prep_chat_kwargs_disables_follow_up() -> None:
    """``plan_query_prep_node`` stages ``with_follow_up`` False."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "How does photosynthesis work?",
            "obs_file_list": [],
        },
    )
    result = await agent.plan_query_prep_node(state)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0114`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_chat_single_shot:[62:69]
==tests.agents.test_review_follow_up_routing:[103:110]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "revised_reports": [
                {"subtopic": "dim1", "revised_report": "Content 1"},
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0115`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_chat_single_shot:[86:98]
==tests.agents.test_review_follow_up_routing:[126:137]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "original_user_query": "Photosynthesis",
            "summary_content": "A review of photosynthesis.",
            "all_raw_doc_list": [],
            "add_doc_list": [],
        },
    )
    result = await agent.follow_up_prep_node(state)

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0116`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[114:120]
==tests.agents.test_review_revised_fan_out:[121:127]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 2,
            "subtopic": "auxin signalling",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0117`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[130:153]
==tests.agents.test_review_review_results_fan_out:[130:159]
    assert "failures" not in result
    assert fake_app.ainvoke.await_count == 1


# ---------------------------------------------------------------------------
# Flag-on worker exception: writes empty sentinel AND FailureRecord.
# ---------------------------------------------------------------------------


async def test_draft_worker_node_exception_writes_sentinel_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker writes ``""`` sentinel AND FailureRecord dict on exception."""
    fake_app = AsyncMock(
        ainvoke=AsyncMock(side_effect=RuntimeError("chat timeout"))
    )
    monkeypatch.setattr(f"{_AGENT_MODULE}.CHAT_APP", fake_app)
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 1,
            "subtopic": "drought tolerance",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0118`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[147:153]
==tests.agents.test_review_revised_fan_out:[167:173]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "task_index": 1,
            "subtopic": "drought tolerance",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0119`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[163:169]
==tests.agents.test_review_review_results_fan_out:[168:174]
    failures = result.get("failures", [])
    assert len(failures) == 1
    rec = failures[0]
    # FailureRecord is a TypedDict — check structural keys, not isinstance.
    assert rec["kind"] == "execute"
    assert "chat timeout" in rec["message"]
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0120`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[28:57]
==tests.agents.test_review_review_results_fan_out:[28:57]
pytestmark = pytest.mark.agent

_AGENT_MODULE = "mcp_server_phytomni.agents.review.agent"


def _build_agent() -> DeepResearchAgent:
    """Construct a ``DeepResearchAgent`` for the review_results fan-out."""
    return DeepResearchAgent(
        review_config=ReviewConfig(),
        sensitive_config=SensitiveConfig.load(),
    )


def _ok_chat_response(text: str) -> dict[str, Any]:
    """Build a minimal chat-completion response wrapping ``text``."""
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------------------
# Prepare node returns empty delta.
# ---------------------------------------------------------------------------


async def test_review_results_prepare_tasks_node_returns_empty_delta() -> None:
    """``review_results_prepare_tasks_node`` acts as a no-op split node."""
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0121`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_draft_fan_out:[73:83]
==tests.agents.test_review_follow_up_routing:[148:158]
    agent = _build_agent()
    params = [
        {"subtopic": "photosynthesis", "knowledge": "snippet-0"},
        {"subtopic": "chlorophyll", "knowledge": "snippet-1"},
        {"subtopic": "stomatal", "knowledge": "snippet-2"},
    ]
    state = cast(DeepResearchState, {"dimension_params": params})
    sends = agent.route_draft_tasks(state)

    assert len(sends) == 3
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0122`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_follow_up_routing:[167:180]
==tests.agents.test_review_review_results_fan_out:[72:85]
    agent = _build_agent()
    dimensions = ["photosynthesis", "chlorophyll", "stomatal"]
    drafts = ["draft-A", "draft-B", "draft-C"]
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": dimensions,
            "draft_contents": drafts,
        },
    )
    sends = agent.route_review_results_tasks(state)

    assert len(sends) == 3
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0123`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_review_results_fan_out:[279:284]
==tests.agents.test_review_revised_fan_out:[340:345]
    agent = _build_agent()
    node_keys = list(agent.app.get_graph(xray=True).nodes.keys())
    assert any(
        key.startswith("review_results_worker_node:") for key in node_keys
    ), sorted(node_keys)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0124`

Rationale:

```text
Similar lines in 2 files
==tests.agents.test_review_review_results_fan_out:[53:59]
==tests.agents.test_review_revised_fan_out:[47:53]
    agent = _build_agent()
    state = cast(
        DeepResearchState,
        {
            "research_dimensions": ["photosynthesis"],
            "draft_contents": ["draft-A"],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0125`

Rationale:

```text
Similar lines in 2 files
==test_a2ui_actions_http:[810:819]
==test_a2ui_contract_fixtures:[75:84]
    "title": "Gene ID",
    "fields": [
        {
            "name": "gene_id",
            "label": "Gene ID",
            "type": "text",
            "required": True,
        }
    ],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0138`

Rationale:

```text
Similar lines in 2 files
==test_api_agent_runs:[279:284]
==test_api_runs_list:[455:460]
    response = await api_client.post(
        "/v1/agents/chat/runs",
        headers={"Authorization": f"Bearer {issued_api_key}"},
        json={
            "arguments": {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0139`

Rationale:

```text
Similar lines in 2 files
==test_api_brief_gene_resolve:[342:357]
==test_api_deep_genome_resolve:[117:132]
    assert metadata.get("resolved_gene_id") == "Os01g0177400"
    assert metadata.get("resolved_species_code") == "osa"
    assert metadata.get("resolve_gene_id") is True


async def test_native_runs_skips_resolver_when_flag_false_or_missing(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=false leaves user_query as-is, and the key is still popped."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0140`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[233:239]
==test_api_runs_list:[630:636]
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0141`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[296:302]
==test_capture_reasoning_normalize:[175:181]
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0142`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[296:303]
==test_capture_reasoning_normalize:[55:62]
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": (
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0143`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[299:306]
==tests.agents.test_chat_agent:[451:458]
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": (
                                "<think>identify chlorophyll</think>"
                                "Leaves capture light."
                            ),
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0144`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[301:313]
==test_result_formatting:[308:320]
                    "content": "",
                    "reasoning_content": (
                        "<think>identify chlorophyll</think>"
                        "Leaves capture light."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 12},
    }

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0145`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[30:38]
==test_api_runs_list:[628:636]
        return {
            "id": "chatcmpl-canned",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0146`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[40:60]
==test_api_runs_list:[639:651]
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setitem(
        server.TOOL_HANDLERS,
        server.PhytomniAgents.CHAT_AGENT.value,
        fake,
    )

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0147`

Rationale:

```text
Similar lines in 2 files
==test_api_deep_genome_resolve:[191:209]
==test_api_design_resolve:[186:204]
    )

    assert response.status_code == 400
    body = response.json()
    assert "user_query" in body["error"]["message"]
    assert not resolver_calls
    assert "gene_id" not in captured


async def test_native_runs_resolver_failure_returns_400(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """DigitalDesignResolveError surfaces as HTTP 400 with the reason."""
    del tasks_db_path
    captured: dict[str, Any] = {}
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0148`

Rationale:

```text
Similar lines in 2 files
==test_api_deep_genome_resolve:[274:283]
==test_api_design_resolve:[266:275]
            "resolve_gene_id": True,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert "species_code" in body["error"]["message"]
    assert "gene_id" not in captured
    assert "species_code" not in captured
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0149`

Rationale:

```text
Similar lines in 2 files
==test_api_deep_genome_resolve:[58:86]
==test_api_design_resolve:[56:84]
        fake,
    )


async def _post_run(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    agent_slug: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """POST one native /v1/agents/{slug}/runs with arguments."""
    auth_header = {"Authorization": f"Bearer {issued_api_key}"}
    payload = {"arguments": arguments}
    return await api_client.post(
        f"/v1/agents/{agent_slug}/runs", headers=auth_header, json=payload
    )


async def test_native_runs_resolves_when_flag_true(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tasks_db_path: str,
) -> None:
    """flag=true rewrites user_query into gene_id and stamps metadata."""
    del tasks_db_path
    captured: dict[str, Any] = {}
    resolver_calls: list[str] = []
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0150`

Rationale:

```text
Similar lines in 2 files
==test_api_run_logs:[266:280]
==test_api_runs_status:[93:107]
        headers={"Authorization": f"Bearer {issued_api_key}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 404


async def test_get_run_logs_foreign_owner_is_404(
    api_client: httpx.AsyncClient,
    issued_api_key: str,
    tasks_db_path: str,
) -> None:
    """A run owned by another user is invisible (404)."""
    RunRegistry(tasks_db_path).create_run(
        RunSpec(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0151`

Rationale:

```text
Similar lines in 2 files
==test_api_runs_list:[549:558]
==test_run_registry:[463:472]
        conn.executemany(
            "UPDATE runs SET created_at = ? WHERE run_id = ?",
            [
                ("2026-01-01T00:00:00+00:00", "run-old"),
                ("2026-06-01T00:00:00+00:00", "run-new"),
            ],
        )
        conn.commit()

```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0152`

Rationale:

```text
Similar lines in 2 files
==test_api_user_context:[40:51]
==test_handlers_scratch:[30:50]
    return cast(
        ServerConfig,
        SimpleNamespace(
            BUCKET_NAME="phytomni",
            TEMP_DIR=str(tmp_path / "fallback"),
        ),
    )


def test_context_helpers_default_to_none() -> None:
    """Verify the contextvars are unset outside a request context."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0153`

Rationale:

```text
Similar lines in 2 files
==test_get_task_status:[39:49]
==tests.agents.test_deep_genome_lifecycle:[54:64]
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="# BriefGene profile",
    )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
    )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0154`

Rationale:

```text
Similar lines in 2 files
==test_get_task_status:[54:60]
==tests.agents.test_deep_genome_lifecycle:[529:535]
    store.apply_work_item_transition(
        reservation.umbrella_task_id,
        work_item_key="smep_analysis",
        status="succeeded",
        summary_markdown="# SMEP summary",
    )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0155`

Rationale:

```text
Similar lines in 2 files
==test_mcp_app_invoke:[266:272]
==test_result_formatting:[755:761]
        "network_task": {
            "task_id": "net-1",
            "output_dir": "/obs/phytomni/net/out",
            "compute_resource": "medium",
        },
        "phytomni_state": {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0156`

Rationale:

```text
Similar lines in 2 files
==test_mcp_app_invoke:[304:317]
==test_result_formatting:[645:658]
        "design_task_result": [
            {
                "task_id": "prot-1",
                "output_dir": "/obs/phytomni/prot",
                "compute_resource": "large",
            },
            {
                "task_id": "prom-1",
                "output_dir": "/obs/phytomni/prom",
                "compute_resource": "large",
            },
        ],
        "phytomni_state": {
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0157`

Rationale:

```text
Similar lines in 2 files
==test_memory_http:[39:44]
==tests.conftest:[399:404]
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_ASYNC_REQUEST)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test"
    ) as client:
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0158`

Rationale:

```text
Similar lines in 2 files
==test_relay_forward_request:[64:74]
==test_relay_openai_routes:[50:60]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            yield client

    monkeypatch.setattr(forward_module, "get_async_client", _factory)


async def _openai_inject() -> dict[str, str]:
    """Return an OpenAI-style operator credential header."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0159`

Rationale:

```text
Similar lines in 2 files
==test_relay_obs_routes:[49:54]
==test_relay_openai_routes:[76:81]
    db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(db))
    return lambda svc: store.create(
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0160`

Rationale:

```text
Similar lines in 2 files
==test_relay_obs_routes:[67:79]
==test_relay_openai_routes:[92:111]
    async with httpx.AsyncClient(
        transport=transport, base_url="http://relay.test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0161`

Rationale:

```text
Similar lines in 2 files
==test_relay_openai_routes:[76:111]
==test_relay_platform_routes:[102:132]
    db = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", str(db))
    monkeypatch.setenv("PHYTOMNI_RELAY_ENABLED", "1")
    store = ApiKeyStore(str(db))
    return lambda svc: store.create(
        user_id="c", scopes=[f"relay:{svc}"]
    ).api_key


@pytest.fixture(name="client")
async def _client_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield an httpx client bound to the relay app over ASGI."""
    monkeypatch.setattr(httpx.AsyncClient, "request", _REAL_REQUEST)
    transport = httpx.ASGITransport(app=_build_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://relay.test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the per-key in-flight relay counter across tests."""
    monkeypatch.setattr(forward_module, "_INFLIGHT", {})


async def test_llm_route_injects_bearer_and_drops_query(
    client: httpx.AsyncClient,
    relay_key: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The llm route strips the caller key, injects the operator Bearer,
    and forwards to the config URL without the client query string."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0162`

Rationale:

```text
Similar lines in 2 files
==test_result_formatting:[479:488]
==test_result_formatting_metadata_contract:[29:38]
    "plan": "1. retrieve data\n2. analyze\n3. report",
    "plan_feedback": None,
    "plan_retries": 1,
    "extracted_tools": ["pyfasta", "pandas"],
    "tool_usages": "pyfasta -i ...",
    "method_context": {
        "upload": {"path": "sop.pdf"},
        "literature": {"hits": []},
    },
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0163`

Rationale:

```text
Similar lines in 2 files
==test_resume_mcp:[107:117]
==test_stdio_progress:[49:60]
    monkeypatch.setattr(
        app_mod,
        "_graph_stream_target",
        lambda _tool, _args: (fake_app, {"user_query": "q"}),
    )
    monkeypatch.setattr(
        app_mod, "_astream_progress_ticks", _fake_astream_progress
    )
    # capture terminal payload path
    monkeypatch.setattr(
        app_mod,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0164`

Rationale:

```text
Similar lines in 2 files
==test_assertions:[21:29]
==test_polling:[63:71]
            "task_id": "task-1",
            "status": "failed",
            "analysis_id": "",
            "output_dir": "",
            "intermediate_report": "# profile",
            "final_report": None,
            "report_stage": "intermediate",
            "report_completeness": "partial",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0165`

Rationale:

```text
Similar lines in 2 files
==test_assertions:[31:36]
==test_polling:[73:78]
            "progress": {"brief_gene_status": "succeeded"},
            "degraded": True,
            "degraded_reason": "1 of 12 optional analyses unavailable",
            "brief_gene_status": "succeeded",
            "failures": (),
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0166`

Rationale:

```text
Similar lines in 2 files
==test_a2a_client:[126:132]
==test_fake_peer_e2e:[151:157]
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0167`

Rationale:

```text
Similar lines in 2 files
==test_a2a_client:[133:141]
==test_fake_peer_e2e:[158:166]
            payload = json.dumps(card_payload).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0168`

Rationale:

```text
Similar lines in 2 files
==test_a2a_client:[293:306]
==test_fake_peer_e2e:[245:258]
        [
            (
                TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED,
                        message=Message(parts=[Part(text="choose")]),
                    ),
                ),
                0,
            )
        ],
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0169`

Rationale:

```text
Similar lines in 2 files
==test_a2a_client:[87:96]
==test_fake_peer_e2e:[113:122]
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        skills=[
            AgentSkill(
                id="annotate",
                name="Annotate",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0170`

Rationale:

```text
Similar lines in 2 files
==test_brief_gene_resolve_query:[367:374]
==test_deep_genome_resolve_query:[57:64]
        return _make_response(
            {
                "gene_id": "Os01g0177400",
                "species_code": "osa",
                "candidates": [
                    {
                        "gene_id": "Os01g0177400",
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0171`

Rationale:

```text
Similar lines in 2 files
==test_client_elicitation:[59:66]
==test_client_progress_callback:[18:23]
    client = PhytomniMcpClient()
    fake_session = AsyncMock()
    fake_result = AsyncMock()
    fake_result.isError = False
    fake_result.content = []
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0172`

Rationale:

```text
Similar lines in 2 files
==test_deep_genome_admin:[38:46]
==tests.agents.test_deep_genome_report:[330:338]
    )
    store.apply_brief_gene_transition(
        reservation.umbrella_task_id,
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    store.seed_plan(
        reservation,
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0173`

Rationale:

```text
Similar lines in 2 files
==test_deep_genome_admin:[41:48]
==test_task_reconcile:[100:107]
        status="succeeded",
        summary_markdown="BriefGene summary",
    )
    store.seed_plan(
        reservation,
        build_work_item_plan("osa", "Os01g0100100", "Os01g0100100"),
    )
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0174`

Rationale:

```text
Similar lines in 2 files
==test_deep_genome_dispatch:[97:103]
==tests.agents.test_deep_genome_dispatch_routing:[439:445]
    submission = RemoteSubmission(
        submitted_task_id="caller-1",
        poll_task_id="remote-1",
        output_dir="/obs/out",
    )
    submit = AsyncMock(return_value=submission)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0175`

Rationale:

```text
Similar lines in 2 files
==test_deep_genome_store:[36:47]
==test_task_registry_migration:[44:55]
    "intermediate_report",
    "report_revision",
    "report_stage",
    "report_completeness",
    "report_updated_at",
    "progress_json",
}


def _legacy_db(tmp_path: Path) -> Path:
    """Create the original four-column task registry schema."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0176`

Rationale:

```text
Similar lines in 2 files
==test_deep_genome_store:[49:62]
==test_task_registry_migration:[81:122]
    conn.execute("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            status TEXT,
            analysis_id TEXT,
            output_dir TEXT
        )
        """)
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?)",
        ("legacy-1", "submitted", "", "/legacy/out"),
    )
    conn.commit()
    conn.close()

    TaskManager(db)

    assert _columns(db) == _EXPECTED_COLUMNS
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT status, output_dir, run_id, user_id, agent, origin, "
            "created_at, updated_at FROM tasks WHERE task_id = ?",
            ("legacy-1",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (
        "submitted",
        "/legacy/out",
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Re-initializing an already-migrated database is a no-op."""
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0177`

Rationale:

```text
Similar lines in 2 files
==test_stream_lifecycle:[228:241]
==tests.agents.test_stream_graph_agent:[427:434]
    for forbidden in (
        "bearer-secret",
        "postgresql://",
        "db-user:db-password",
        "SELECT secret_token",
    ):
        assert forbidden not in evidence
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0186`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0187`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0188`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0189`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0190`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0193`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0194`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0195`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0196`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0197`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0198`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0199`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0200`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0201`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0205`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0206`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0209`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0210`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0211`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0212`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0213`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0214`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0215`

Rationale:

```text
Too few public methods (1/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0216`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0217`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0218`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0219`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0220`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0221`

Rationale:

```text
Too few public methods (0/2)
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0222`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0223`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0224`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0225`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0226`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0227`

Rationale:

```text
pylint: disable=broad-exception-caught
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0228`

Rationale:

```text
pylint: disable=contextmanager-generator-missing-cleanup
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0229`

Rationale:

```text
pylint: disable=contextmanager-generator-missing-cleanup
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0232`

Rationale:

```text
tool.pylint.format.max-module-lines=1100
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0235`

Rationale:

```text
tool.pylint.main.ignore='typings'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0236`

Rationale:

```text
tool.pylint.main.ignore='.venv'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0237`

Rationale:

```text
tool.pylint.main.ignore='.mypy_cache'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0238`

Rationale:

```text
tool.pylint.main.ignore='.ruff_cache'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0239`

Rationale:

```text
tool.pylint.main.ignore='.pytest_cache'
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0241`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0242`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0243`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0244`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0245`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0246`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0247`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0248`

Rationale:

```text
pylint: disable=protected-access,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0249`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0250`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0251`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0252`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0253`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0254`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0255`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0256`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0257`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0258`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0259`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0260`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0261`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0262`

Rationale:

```text
pylint: disable=protected-access
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0263`

Rationale:

```text
pylint: disable=too-many-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0264`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0265`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-positional-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0266`

Rationale:

```text
pylint: disable=too-many-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0267`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-positional-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0268`

Rationale:

```text
pylint: disable=too-many-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0269`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals,too-many-statements
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0270`

Rationale:

```text
pylint: disable=too-many-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0271`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0272`

Rationale:

```text
pylint: disable=too-many-instance-attributes
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0273`

Rationale:

```text
pylint: disable=too-many-instance-attributes
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0274`

Rationale:

```text
pylint: disable=too-many-instance-attributes
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0275`

Rationale:

```text
pylint: disable=too-many-lines
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0276`

Rationale:

```text
pylint: disable=too-many-lines
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0277`

Rationale:

```text
pylint: disable=too-many-lines
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0278`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0279`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0280`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0281`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals,too-many-statements
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0282`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0283`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0284`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0285`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0286`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0287`

Rationale:

```text
pylint: disable=too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0288`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0289`

Rationale:

```text
pylint: disable=protected-access,too-many-locals
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0290`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-positional-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0291`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-positional-arguments
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0292`

Rationale:

```text
pylint: disable=too-many-arguments,too-many-locals,too-many-statements
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0293`

Rationale:

```text
pylint: disable=wrong-import-position
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0294`

Rationale:

```text
tool.pymarkdown.plugins.md013.enabled=False
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0295`

Rationale:

```text
error
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0298`

Rationale:

```text
ignore:ssl.PROTOCOL_TLS is deprecated:DeprecationWarning
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0299`

Rationale:

```text
tool.ruff.lint.ignore=['ASYNC109']
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0300`

Rationale:

```text
noqa: ASYNC110
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0301`

Rationale:

```text
noqa: ASYNC110
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0302`

Rationale:

```text
noqa: ASYNC110
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0303`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0304`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0305`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0306`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0307`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0308`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0309`

Rationale:

```text
noqa: ASYNC240
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0310`

Rationale:

```text
noqa: E402
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0311`

Rationale:

```text
noqa: E402
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0312`

Rationale:

```text
tool.ruff.lint.per-file-ignores.typings/**/*.pyi=['N802', 'N803', 'N815']
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0313`

Rationale:

```text
tool.ruff.lint.per-file-ignores.typings/**/*.pyi=['N802', 'N803', 'N815']
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```

### `SAE-TMP-0314`

Rationale:

```text
tool.ruff.lint.per-file-ignores.typings/**/*.pyi=['N802', 'N803', 'N815']
```

Counterfactual:

```text
Remove or refactor after review.
```

Risk:

```text
Suppression can hide a future regression.
```
