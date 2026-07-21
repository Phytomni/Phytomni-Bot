# Static-analysis exemption ledger

This file is generated from `static-analysis-exemptions.toml`.

Regeneration:

```bash
uv run python scripts/check_static_analysis_exemptions.py render-docs
```

- Schema version: `1`
- Policy default: `deny`
- Authorized records: `207`

## Informational counts

| Tool and rule                                                     | Records |
| ----------------------------------------------------------------- | ------: |
| `flake8:E203`                                                     |       1 |
| `flake8:W503`                                                     |       1 |
| `mypy:ignore_missing_imports`                                     |       2 |
| `mypy:misc`                                                       |       2 |
| `mypy:prop-decorator`                                             |       1 |
| `pylint:C0103`                                                    |       1 |
| `pylint:C0116`                                                    |       1 |
| `pylint:R0801`                                                    |      82 |
| `pylint:R0903`                                                    |       7 |
| `pylint:R0913`                                                    |       1 |
| `pylint:R0917`                                                    |       1 |
| `pylint:W0613`                                                    |       1 |
| `pylint:broad-exception-caught`                                   |       3 |
| `pylint:contextmanager-generator-missing-cleanup`                 |       2 |
| `pylint:max-module-lines`                                         |       1 |
| `pylint:path-ignore`                                              |       4 |
| `pylint:protected-access`                                         |      44 |
| `pylint:too-few-public-methods`                                   |       3 |
| `pylint:too-many-arguments`                                       |      10 |
| `pylint:too-many-instance-attributes`                             |       3 |
| `pylint:too-many-lines`                                           |       3 |
| `pylint:too-many-locals`                                          |       9 |
| `pylint:too-many-positional-arguments`                            |       2 |
| `pylint:too-many-statements`                                      |       1 |
| `pylint:wrong-import-position`                                    |       1 |
| `pymarkdown:md013`                                                |       1 |
| `pytest:error`                                                    |       1 |
| `pytest:ignore:ssl.PROTOCOL_TLS is deprecated:DeprecationWarning` |       1 |
| `ruff:ASYNC109`                                                   |       9 |
| `ruff:ASYNC110`                                                   |       3 |
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
| `SAE-TMP-0001` | flake8 | E203 | structural | config | config | .flake8 | [flake8].extend-ignore | `sha256:ee3ff2f285462681d7e4dd5eed33cbf4cd5ef1d73ca986281e086dc5ef2457a8` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0002` | flake8 | W503 | structural | config | config | .flake8 | [flake8].extend-ignore | `sha256:1d93293eb8e91ba241c13a2db0c100dc24b5305c4b0f429faa5ca2f334bfccc1` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0006` | mypy | ignore_missing_imports | temporary | config | config | pyproject.toml | tool.mypy.overrides[0].ignore_missing_imports | `sha256:527e6efc74769b1228c507a1b8b80620eb8ac8b9870681cad036239346b48e93` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0007` | mypy | ignore_missing_imports | temporary | config | config | pyproject.toml | tool.mypy.overrides[1].ignore_missing_imports | `sha256:34f322754c32f50efaf7a19013545c63f95683102c5f7eefefb2e6fdabd638c2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0009` | mypy | misc | structural | inline | symbol | tests/unit/interop/test_capabilities.py | test_capability_is_frozen_and_qualified_name_is_canonical | `sha256:50c5686aeb514f0ea385b0f5efcb20543fb02b479f0a05b4b32116c2e38f65fe` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0010` | mypy | misc | structural | inline | symbol | tests/unit/test_deep_genome_store.py | test_store_exports_frozen_contract_models | `sha256:da11d568e3816c864121904ffa5d00103600ebf471ec6a5bbefc12277ad4761c` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0011` | mypy | prop-decorator | structural | inline | symbol | src/mcp_server_phytomni/api/schemas.py | FileUploadResponse | `sha256:243d7b24f3e529af946567903305f2f5f09ad24b6316e1063a7ed27a93107a6c` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0014` | pylint | R0801 | temporary | diagnostic | pair | scripts/compare_gauss_queries.py | 294:301 | `sha256:ca2b6d468bc318f627b0fe79e4d2116a77ed25e26cc4ec75aff51feb8b8aab15` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0015` | pylint | R0801 | temporary | diagnostic | pair | scripts/compare_gauss_queries.py | 91:99 | `sha256:9f9f3a0f8627709ab7819ee411220c1052aca195cca63666c9e6cb61722409cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0017` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/helpers.py | 17:25 | `sha256:2bde4ceca2e89f4d0dad6f8725a9c3d0c698832307b62142e73dbd56081c53ec` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0018` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/helpers.py | 19:25 | `sha256:c3d8f0f3bfc38a05eedccf0a9944fa14e3875058532624f30f85eb678652d56c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0019` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 341:349 | `sha256:290742346aa7c7c34844303e39f49a3a4c084e52891b671d3920b8c4dd1b9dc7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0020` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 342:348 | `sha256:b1101f1725b92758ac965df2e52c683ebb72eff13f3b3441df98302e87b3eb5e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0021` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/pylint.py | 101:115 | `sha256:c87f948de5cea76c38e86b5c43164ae82e0768d8fd10d71b9dd6dce1af1ed334` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0022` | pylint | R0801 | temporary | diagnostic | pair | scripts/static_analysis/collectors/source.py | 84:98 | `sha256:67feab30f45f9e474e0c786410eb52f9e3d687f54cc727e088b39882af857abd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0028` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/__init__.py | 42:47 | `sha256:04b1f52c7567afcb17be02df79c0cadd82b90a00fa8a07e06ab48b5a2dafafb8` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_nodes.py, tests/agents/test_analyst_routers.py, static-analysis-inventory |
| `SAE-TMP-0029` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/agent.py | 39:44 | `sha256:db1a39a7296629412f62345cacd4a0f73d49c06d2acf3b459fe10a81dcf460dc` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_nodes.py, tests/agents/test_analyst_routers.py, static-analysis-inventory |
| `SAE-TMP-0040` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/planning.py | 101:107 | `sha256:1cc992aafdda044d48f51d390b1f04303cd2674f2a26b2c9b980e3a444116463` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_planning_helpers.py, tests/agents/test_analyst_submission_helpers.py, static-analysis-inventory |
| `SAE-TMP-0041` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/state.py | 114:119 | `sha256:b9c858b4756d1eeba0e3575fea150ea4750ceadcf06fb842cd871d087c87a349` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_io.py, tests/agents/test_data_chat_subgraph.py, tests/agents/test_data_knowledge_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0042` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 119:124 | `sha256:acc5191ff5b112c006b066722a99e000b132a80da09bc5169648a61897a5962c` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_deep_genome_brief_gene_mount.py, static-analysis-inventory |
| `SAE-TMP-0043` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/core.py | 119:130 | `sha256:f033014ca9266166d94e7a23b1371c14297738e430692fed0237a39365206613` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_brief_gene_knowledge_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0046` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 81:87 | `sha256:18442c096ed583a858acc579252c4b83e8736b791721a8fc1410d959d20bc96a` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_brief_gene_knowledge_subgraph.py, tests/agents/test_review_retrieve_fan_out.py, static-analysis-inventory |
| `SAE-TMP-0047` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 88:95 | `sha256:585e12c9cfdc39454cc842d0f5d994977f8f4b1883badbc2c8c7540676356192` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_brief_gene_knowledge_subgraph.py, tests/agents/test_review_retrieve_fan_out.py, static-analysis-inventory |
| `SAE-TMP-0052` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 106:111 | `sha256:bced20b31d1f24e53dbf50abc7462eeaaa5c709ed8fc09081f208f3992fd09bb` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_deep_genome_brief_gene_mount.py, static-analysis-inventory |
| `SAE-TMP-0053` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 130:143 | `sha256:c09286b8f6a95ac45cc85c1daee7a3e0ca52fb6143482344b0841ba433d82290` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_deep_genome_brief_gene_mount.py, static-analysis-inventory |
| `SAE-TMP-0054` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 61:67 | `sha256:f33cd96ed68f9985a80483c8f061112d267e9818e32b3ac5bbb2e47f31f2b2f6` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_deep_genome_brief_gene_mount.py, static-analysis-inventory |
| `SAE-TMP-0055` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/state.py | 67:76 | `sha256:250f8f011043370e361a2a149e6cd8517793e67631733c00cf8209112b9157dc` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_deep_genome_brief_gene_mount.py, static-analysis-inventory |
| `SAE-TMP-0056` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/chat/a2ui_graph.py | 197:207 | `sha256:c2d9b397b9cf9fdf2daec3d5a3281cc05f7c974d14a938108a47e972dc4c9292` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/server/test_a2ui_actions_http.py, static-analysis-inventory |
| `SAE-TMP-0061` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | 172:181 | `sha256:2c939f306bfc192d1b4ccf2554102891e23b77d00d43855201693dcb676bfc6b` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_deep_genome_work_items.py, static-analysis-inventory |
| `SAE-TMP-0062` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 378:385 | `sha256:e0cd24dbe16f751325b6a9d60a10e3a8e4fe6eccfba8cb586e3df4d1aa2eb7bd` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0063` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 506:518 | `sha256:eb821e4f9d43cb8e7d63842145181a1ce0917da9327a47d1b643eebf9e0bd456` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0064` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 558:567 | `sha256:0627fefe4483668befa42c7e51c26cd08660fe4b47df18739b5d4fe90ba74343` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0065` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 565:579 | `sha256:b2f685bc5dcf5396ad18b7e564ec17ea22990763b2a47f36e9c2a18ed52c5660` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0066` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 568:584 | `sha256:75d6daa79ef9e397412d70daa0a2ab61a54694ac206a3baa5519a136088e43cd` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0067` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 664:694 | `sha256:aae20b708118a245d565bf4dba59ddfb08f4a2b3f5da3f168dd44d2f26201223` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0068` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/environment/agent.py | 89:96 | `sha256:412bbae97443018da3d59a4a22453b8e01b100e4a28028e36d6dbf2ee3982c7b` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_environment_chat_subgraph.py, tests/agents/test_evolution_chat_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0069` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/environment/graph.py | 181:187 | `sha256:50c4d2220d590f4303e8987fa4ce2430000db42288370545fbcd3e29d748d315` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_environment_analyst_subgraph.py, tests/agents/test_evolution_analyst_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0071` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/shared/chat_subgraph.py | 101:142 | `sha256:e6c553359093f37502c15611b3d82295a314c4d2579ff20ffb23bab3a54980de` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_chat_subgraph_wrapper.py, tests/agents/test_knowledge_subgraph_support.py, static-analysis-inventory |
| `SAE-TMP-0073` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/api/relay/__init__.py | 20:25 | `sha256:11110a31a61699cdd16bc8f75aa9c481305c2dad14bf2bb7471c6c45723cd27a` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/server/test_relay_mount.py, tests/server/test_relay_audit_routes.py, static-analysis-inventory |
| `SAE-TMP-0074` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/api/relay/audit_filter.py | 27:38 | `sha256:e403f78e603f501a9592111cdd3963a7670f069c42cb5d1e1755b764d1dc32e9` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_relay_audit_filter.py, static-analysis-inventory |
| `SAE-TMP-0075` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 16:28 | `sha256:0a106c71d1e188f33ba116a1a15980da2ccbd8924042f13b31d0dd5df3f2585c` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/config/test_required_env.py, tests/unit/test_defaults.py, static-analysis-inventory |
| `SAE-TMP-0076` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 41:46 | `sha256:b181294b667a5a5e3b3cc601d177d1d5e50806e12df472c917d370454afa17f9` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/config/test_required_env.py, static-analysis-inventory |
| `SAE-TMP-0078` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/mcp/app.py | 776:781 | `sha256:4a57d42c7066ead11e72824504ea38c35100a972dea9d50d624994daf75dfac4` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/server/test_mcp_app_invoke.py, tests/server/test_mcp_app_validation.py, static-analysis-inventory |
| `SAE-TMP-0079` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/mcp/result_formatting.py | 1148:1155 | `sha256:e30cd2c0562ade084c28cd8c70e311f403d6e4e7ba05409bd687cac3e3bacbc4` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_result_formatting_projection.py, static-analysis-inventory |
| `SAE-TMP-0083` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 45:52 | `sha256:781cbd4798bbd051136fe6306e69070b99da317486e9f110b66c4c53f764ebd8` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_models.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0084` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 59:64 | `sha256:dab91daad1dc692da89b2d3c4bedfcd081201a6cd5e592f6d9f289f0ad825f66` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_models.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0085` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 73:79 | `sha256:02a0ef0f7f5041c275b28c1c4c24c010eae62e1de902e15478e70af3b9aa9b42` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_accessor.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0086` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/migrations.py | 23:31 | `sha256:4f06fd5ec9e45a0c63ea3f65946f56998710e5123f0135fc1bc20ca49b9bbe3a` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_migrations.py, static-analysis-inventory |
| `SAE-TMP-0087` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_analyst_graph_io.py | 37:60 | `sha256:7df9fed3886f917a843ce0b27b08bc06111ab61bf3f15c8d6cb7bc1c73814ba3` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0092` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_brief_gene_chat_subgraph.py | 71:79 | `sha256:36c489b726b7409ac52d963563d5e3c3db3049c965e714694d291c98f53e459c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0093` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_brief_gene_knowledge_subgraph.py | 81:86 | `sha256:0c5f1d7ed62b15af3b87f94ba171987177371c7e209c1daad28c40fc6c396cad` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0100` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 208:218 | `sha256:6ec90dba8d40b01c4189bf5264b3872181875855cfee0dfb11b5277a33bbd81e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0101` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 236:246 | `sha256:e755caa3cded6202c151581162fcd5492923c77cec5bf4cd06c61650cbd88ebe` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0102` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_deep_genome_dispatch_routing.py | 247:256 | `sha256:14cda471c334b08b8932ae449953e69923ce5a21b05940575ba4bcb750879d89` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0107` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_environment_agent.py | 168:176 | `sha256:4620aee296dabe883d84e3fc303a3a4d7fd87eee6bff62a845d9ed2318b34a74` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0108` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_graphs_manifest_export.py | 179:194 | `sha256:bf3ab1c0c50e8432330c1340c5fa446ca0e2f69697f51353579a6054cdff6615` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0109` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_resume_kernel.py | 25:42 | `sha256:4ad39b4ad4b4823fe799020de33c01bfd5161991446aff20e5be60d87f550eae` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0110` | pylint | R0801 | temporary | diagnostic | pair | tests/agents/test_resume_kernel.py | 43:48 | `sha256:e44e55cdded49e86109ef57098c78ebda86bba0f2946ece45fa0ec9ed1c314db` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0125` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_a2ui_actions_http.py | 810:819 | `sha256:d30623a9d2caa6c8f0ea3c64da0ad0d8b8d7ed8e0e45dfa8300d9198f8efbda1` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0141` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 270:276 | `sha256:f9c3289d6c898a889a1dfd3e5f8e2cd0be30da770c82e23e4ed98884788a2cfe` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0142` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 270:277 | `sha256:7c7c4f6bd04c654870bcbb3498637d9d1ffc2af0a7f7230c231dcecfb7588ba4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0143` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 273:280 | `sha256:2852957544508ff8577b6510cde1ecef104df64ae5747843bbce8dca9f8bd965` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0144` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_chat_completions.py | 275:287 | `sha256:873179933a1be98afef2b8046639aecc9bf99f4875334e80fb36739322026a5c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0150` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_run_logs.py | 266:280 | `sha256:a50099a104216d972c604e94c37fd02ad914cb71b6efddf52a2497c4b71c2afe` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0151` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_runs_list.py | 550:559 | `sha256:0d25731b5f8cd95112099bf4a63f1347f6a749a52a53df03a1139f9b11af45d4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0152` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_api_user_context.py | 40:51 | `sha256:d070c5d26bcb1a3a76655010cc1e2066842679987463a6520c2e4f896470af51` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0153` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_get_task_status.py | 39:49 | `sha256:6c428246112060c3705f0bd8095bd141da283c3bd9c6ceda5da9009c926a6cbd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0154` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_get_task_status.py | 54:60 | `sha256:d2d3b3f8667794cb7d5957c08d3d429d4c37c0ab86972dbef5b65d510c4b53cd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0155` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_mcp_app_invoke.py | 266:272 | `sha256:2ffab28ca78e35f68ffce562e642663c93ef659e6d1e80fc65e9e7c5271ad3bf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0156` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_mcp_app_invoke.py | 304:317 | `sha256:f6175ef13c21d05adb80c45ca307f25cfbf594357c0c7e86bf6d2a44d7001726` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0157` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_memory_http.py | 39:44 | `sha256:53523ba52adeb85599e4208c9fb09c7547bf6b963993602ada44250a976677cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0158` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_forward_request.py | 64:74 | `sha256:eb7c75d69effca7d17ae04846f5feb2d7ac869090b92381125ac43d1aab36f37` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0159` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_obs_routes.py | 49:54 | `sha256:c50d4d55fd65c090198f5e50a55ff28a38127f0cfa53ba459a00df3bd8da9dbc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0160` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_obs_routes.py | 67:79 | `sha256:41c0b03586d07944630aaf83607bbca72966b829ee988c0a6e94e4d51542656e` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0161` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_relay_openai_routes.py | 76:111 | `sha256:90d03cbc6cfa912e6c79ba754c576d80abf09250ca899f3d180038494cc00bfb` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0162` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_result_formatting.py | 479:488 | `sha256:60be63356afa1ef5d3f3b186a96769cb6661fadc26e383fcd1b6481b61229dd4` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0163` | pylint | R0801 | temporary | diagnostic | pair | tests/server/test_resume_mcp.py | 104:114 | `sha256:4dbbc1854fd72ac53af9cd850a1e453ef7c0bc583d0cc5ecfa4f2a2df4b5f0f6` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0164` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/e2e/test_assertions.py | 21:29 | `sha256:add2ea92737e0640446ee8ba333547f9d8f321dece5f04a7667ac7f7b18d2f8b` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0166` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 126:132 | `sha256:ecf2324d0c1b39b2f5ab6c1ebb468afc9262b3a3dee182cd9a7ccd2bf6cca2a7` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0167` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 133:141 | `sha256:2c326a038c82a3e01dbe2752e30ce9836eebc95688bd6ae65fcefb2711623434` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0168` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 293:306 | `sha256:eb3e93560d9c6607712d8955e1858b2e712515c029d2c14977024abe120064e5` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0169` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/interop/test_a2a_client.py | 87:96 | `sha256:79e228ba92766d992ece14a5cc1de66540efa4031b0094d106a707ac6dbede6c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0171` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_client_elicitation.py | 59:66 | `sha256:975f355cd6c2a65dca9d57186615c419eeda303eac5e94c7fdd0adfb91f399cf` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0172` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_admin.py | 38:46 | `sha256:d31703b04d8b444d3e963b1cc94534a6a2a39ec56fe231841e5ec923d2b20c11` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0173` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_admin.py | 41:48 | `sha256:4303b679e5e955b1be7936f32b4fc48d9634ff57857c088f074419720f9f5cd2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0174` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_dispatch.py | 97:103 | `sha256:edba189ae68899ee52de5400d60bd3c2e65782e445172a0b6860ace817535c43` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0175` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_store.py | 39:50 | `sha256:aa7f4443755b41ff47174f62a6aac518efd4fb796d5af7db82650ceb763d6560` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0176` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_deep_genome_store.py | 52:65 | `sha256:7196ce45cf674e83d55488b732a0eea6b247a4fc6e465683d083bcd6ed0c2b9c` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0177` | pylint | R0801 | temporary | diagnostic | pair | tests/unit/test_stream_lifecycle.py | 228:241 | `sha256:9841db38ab87a9cc9ea8495614647541015f3013773fa3a52a42c315377a2dd6` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0194` | pylint | R0903 | structural | diagnostic | symbol | tests/agents/test_deep_genome_submit.py | \_FakeApp | `sha256:1f7f51708ece122db06fccc8c565c771ccda09fd3dab04360277d25248e3fef0` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0200` | pylint | R0903 | structural | diagnostic | symbol | tests/conftest.py | \_build_fake_obs_client.\_FakeObsClient | `sha256:2dc747295b1e436a070df77a883fd813565b56d9c86de87801b81cfbb178fae7` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0211` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/e2e/test_polling.py | test_http_poll_records_distinct_monotonic_revisions.Client | `sha256:f4a6bb85b202f6f9be1a4bdf895fd80b219a442eab4fd5f04fb29cd46b4c3d93` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0213` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/interop/test_fake_peer_e2e.py | \_FakeMCPServer | `sha256:0dcf2d975d525a931a08dd914e21c20836ea700969650768d93515b034f55122` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0214` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/test_api_file_upload.py | \_ChunkedUpload | `sha256:eda00c1f02071e7fa974cdef673f09b7b2efc3b1302890c475dbc5ed46cefe1d` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0215` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/test_storage_error_sanitization.py | \_ExplodingObsClient | `sha256:826980652eb87a9cc733947dd7a6328efa942f2f98b5cb373e5db5d4ae417825` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0222` | pylint | broad-exception-caught | structural | inline | symbol | e2e/helpers/polling.py | \_reconciled_task_state | `sha256:58b3b2f3bb4ee5b58ee8ca5380146a1ab5d43c18bff5e36510cb912eece854a3` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0223` | pylint | broad-exception-caught | structural | inline | symbol | src/mcp_server_phytomni/api/a2ui_runtime.py | project_review_interrupt | `sha256:54078161cb8bf1725f6e4eec15a243bb9cd8318b335fa825016ecc66e0f40d2c` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | tests/server/test_a2ui_review_http.py, tests/server/test_a2ui_runtime.py, static-analysis-inventory |
| `SAE-TMP-0227` | pylint | broad-exception-caught | temporary | inline | symbol | src/mcp_server_phytomni/mcp/stream_lifecycle.py | project_stream_failures | `sha256:0fa2621f7153da1bff3a4df5a0a23d14425aa3d17b782f9545d0c2e657296557` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0228` | pylint | contextmanager-generator-missing-cleanup | structural | inline | span | tests/agents/test_cache_candidates.py | — | `sha256:1a93e1b05a8cfa872aeddc39c7850189f9089c16b59a0d36c97909cc4341ae03` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0229` | pylint | contextmanager-generator-missing-cleanup | structural | inline | span | tests/conftest.py | — | `sha256:8051cd3af754f03cba8b655218a54168e8867d393d6ed998ce98de611d0b7b9a` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0232` | pylint | max-module-lines | temporary | config | config | pyproject.toml | tool.pylint.format.max-module-lines | `sha256:56ab2666391bfe20e49b31efb25773c9fa08523cec25d451bac69ed8516cd17f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0236` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:38fd2748eed7f3803e3176f0ef992ae35b8441cfc99a8e873d01821f62c700eb` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0237` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:3e1bc269c16210ee4149f6c708497a514291d2716f41ad7186c731fe6fce7ea3` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0238` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:829ec7992def978f01ad8efae0a0ee64c5e1767cfeb76f3a13236dc4ed5cef23` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0239` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:b7c41d52ad3e667583ed5bc27c3cffee33f6c621371befb228c17357718fc94a` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0241` | pylint | protected-access | structural | inline | symbol | tests/agents/test_analyst_graph_nodes.py | \_capture_create_payload | `sha256:cd6697f92e2f53b8be8ff1d45348694b78b636827525788f5ca1dab931844253` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0245` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_chat_subgraph.py | test_dispatch_chat_uses_subgraph | `sha256:dc8cf6a9d64081107dcb8c0509cf9b38ffc98a1931b77e52d26b7fee2d4383eb` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0246` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_deep_genome_generic_dispatch_is_submit_only | `sha256:d7231430fe9c4901f96ab215506cd45a2986336a0c009b0bf3ebb77dc60d81da` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0247` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_knowledge_subgraph.py | test_dispatch_knowledge_uses_subgraph | `sha256:e738b7820e753539f447e164a30d465df7abba5f27a3730d42851135fa2b38aa` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0248` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_all_optional_failures_preserve_profile_and_fail_owner | `sha256:7c4cc25324af4c7a96948142dcd0ffdebb1f0547852b87a5ea4d389d83ccd804` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0250` | pylint | protected-access | structural | inline | symbol | tests/agents/test_design_helpers.py | test_analysis_prompt_parts_rejects_unknown_type | `sha256:5920289e37592f4bf00a38fa591f263313e4ad7774c9a368f018f6090560b37c` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0251` | pylint | protected-access | structural | inline | symbol | tests/agents/test_network_analyst_subgraph.py | test_dispatch_request_carries_to_id_as_target | `sha256:7b00f3c4d7abe53a1e197b9f01f9b447860703561be848f801fd9566b1be0395` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0252` | pylint | protected-access | structural | inline | symbol | tests/agents/test_research_analyst_subgraph.py | test_submit_task_propagates_failed_status | `sha256:703861b6c2122568233a161f3323b921ed51aa617a31c181e20085e857a2f98e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0253` | pylint | protected-access | structural | inline | symbol | tests/agents/test_research_chat_subgraph.py | test_extract_goals_uses_chat_subgraph | `sha256:e779e814ad5960cc1e61077d79408530084759acc64e8a04347db5900bf0a743` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0255` | pylint | protected-access | structural | inline | symbol | tests/agents/test_review_add_query_failures.py | test_feedback_rag_failures_empty_when_no_add_queries | `sha256:ea1bd79bc055e411a3e1bb27595e369d13a9e6be0644e57bb7e949128bb161b6` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0261` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_async_gc_coalesces_concurrent_passes | `sha256:0dcac7edf70b73e87d365ab2a55a6988025320b3309f742972b4e93446d03736` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0262` | pylint | protected-access | structural | inline | symbol | tests/server/test_stdio_progress.py | test_progress_forwarded_when_token_present | `sha256:f119c910e90f018323907d6760b625893d289a739e1ea4ee80765553e81f64ac` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
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
| `SAE-TMP-0285` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/api/app.py | \_stream_chat_completion | `sha256:8564f40fa2157f740ee9f7bff75d93ae929acea50965d862fbdd951cee5c5ecd` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0287` | pylint | too-many-locals | temporary | inline | symbol | src/mcp_server_phytomni/mcp/result_formatting.py | \_format_design_result | `sha256:8628b792cea0d61f8299df4d795e861c92b404813e5abe827b980f211edbe83f` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0288` | pylint | too-many-locals | temporary | inline | span | src/mcp_server_phytomni/storage/uploads.py | — | `sha256:d713586935bbfb52b818a5e473e7d2169737526f18839e9196e5831051e4c466` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0289` | pylint | too-many-locals | temporary | inline | span | tests/agents/test_deep_genome_lifecycle.py | — | `sha256:c89456f6ae1c750fc0f451d10d4b0885a15b45a3ff3d5623b86b86decb5721ba` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0290` | pylint | too-many-positional-arguments | temporary | inline | span | src/mcp_server_phytomni/agents/data/nl2sql.py | — | `sha256:5070abd4d3f8904dfe8375cdec6872bd6d155615cc6bd4b54f914cf5e07c75d2` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0291` | pylint | too-many-positional-arguments | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | DeepGenomeDispatchMixin | `sha256:0798e8ccfb5acef4a3251eb0b379d705d9bd8adf987f3f9f95c931665356ccbc` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0292` | pylint | too-many-statements | temporary | inline | span | src/mcp_server_phytomni/api/app.py | — | `sha256:d31ea0d7a171aebd3fbf1bd2c0eca47443f9cf55147649ba476c187710e62378` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0293` | pylint | wrong-import-position | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:637afabc246f45d9145f3aa93a39fe94d65fb830b2067196e8f33d8b77ed1847` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0294` | pymarkdown | md013 | structural | config | config | pyproject.toml | tool.pymarkdown.plugins.md013.enabled | `sha256:34d8c475a236a211d36db927ee2de462f03fdb4f3f07864359540255c750bf01` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0295` | pytest | error | structural | config | config | pyproject.toml | tool.pytest.ini_options.filterwarnings[0] | `sha256:0aa661937816a1fb17f333eb9a372e15f3093ed7bf8b75bc5d904d38422ef20f` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0298` | pytest | ignore:ssl.PROTOCOL_TLS is deprecated:DeprecationWarning | temporary | config | config | pyproject.toml | tool.pytest.ini_options.filterwarnings[1] | `sha256:3b0438047d9767b1eb34f8a245bfa1e11abe24c3fc8011aee6a4f08905aef12a` | bot-maintainers | 2026-07-17 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0300` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/report.py | \_write_async | `sha256:a7cb1e8424467f0727f7a3b99bf30356d9bb18b99462d892fbf92402f43dd198` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0301` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/api/run_lifecycle.py | purge_expired_runs_best_effort_async | `sha256:d9bfcdb38455dce2970fd011c237a1131de5632e63d545189a9d87480a21b017` | bot-maintainers | 2026-07-20 | 2027-01-20 | — | — | tests/server/test_run_gc_background.py |
| `SAE-TMP-0302` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/api/relay/obs.py | \_wait_obs_future | `sha256:a8f3e3adef4dab97a6423e96bc1f91159c519a5060e52a58156302de97e99e33` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0310` | ruff | E402 | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:5bb9d315b606c4e79f7f08a5a3896da1ae9fe6ea43c2d6d2055599a1c4e33f40` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0311` | ruff | E402 | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:9e583e7c25de4e2c0f6b97674641e1e427cb71f230c2aa96fb00b4aa48ddf6cf` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0312` | ruff | N802 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/\*.pyi | `sha256:bf0326cb042985ec93478cbec49d2e724864485a45acf6ad1e15bc9d520eb9a4` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0313` | ruff | N803 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/*.pyi | `sha256:04fd5950474f7a9f1e297946d6fbe12928593497186dbe231583d79859f82d30` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0314` | ruff | N815 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/\*\*/*.pyi | `sha256:c46f9c1d9fbddac831ac4f216f43c4ff6703ad4274821d10b4f0186703a912e0` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0315` | pylint | protected-access | structural | inline | symbol | tests/agents/test_analyst_graph_nodes.py | test_submit_output_dir_forwards_input_fingerprint | `sha256:854cb4451cc07c0b5a5399e4c782adb77e2a6ce896ed0e39709ba05851d78eaa` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0316` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_dispatch_coordinator_receives_effective_poll_id | `sha256:ff5a3cfe8bf5d4cf7443bd0ec5540f676d620ffcb1e4139a1f5057275463540b` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0317` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_non_transferred_type_routes_subgraph | `sha256:23079a67586d9358ab26bd2222ab2d62c607ad2726a4303a8ca7ff4c34b09458` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0318` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_prepare_tasks_includes_protein_structure | `sha256:70ee5d63c40a1eb2a3e381fddb60eb0611ed8fb8d4778530023df3fe78d2ab62` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0319` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_promoter_routes_to_wrapper | `sha256:b882c1ce446460ec090b3628d6746c574d8099b4abec7812af94f2dd10abf44e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0320` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_protein_structure_routes_to_wrapper | `sha256:21b51d588eb6960f247f06d3d4e503ace1fd66b78dba31785ce83b75bca4ec01` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0321` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_after_brief_gene_reaches_preparation_only_when_enabled | `sha256:d761da1d331ff50a9e6cd62343fa40f554b016080d8bee7754b15ca69795679e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0322` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_analyst_tasks_sends_design_to_design_node | `sha256:60cf736f268c9561d50f890b0ebf4e7ca0bd27a6e4814546ddd5e5a13f7904ca` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0323` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_analyst_tasks_sends_each_generic_to_its_own_node | `sha256:a1a47aff502651142a268a3f6a5dca43b362b182aa4bd782c085a62d6e2e4cc0` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0324` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_analyst_tasks_sends_evolution_to_evolution_node | `sha256:e03c1bbefc25917d063112a7f218cde783b57a08737edee6dff8d8e5b9e460ae` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0325` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_experiment_skips_protocol_when_analyst_disabled | `sha256:46a128372113d8ff065aff1949064e55f2eaa35fd2f45efd6df8dd06b70484d9` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0326` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_start_waits_for_brief_gene_before_task_preparation | `sha256:dbda426b0fdb2d10ad2e90ba6baafc06f5dac5cf5b79b34a558c8ef44ac5b334` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0327` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_synthesize_preserves_skip_fixture | `sha256:ab989c3cc8eefb9243ab93b39b0ad63ae7688af3684805b6e86bf2db17c8cb77` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0328` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_synthesize_rejects_all_terminal_failures | `sha256:9fb0e72047279d6da6017157a24f27374ff7e97110369dc5423f1a7b497244a1` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0329` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_synthesize_waits_for_every_concrete_work_item | `sha256:6038e2fff5d817a8a2b2ef2b9b9966da39c514e167d4039a52a046cc28760666` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0330` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_brief_gene_success_is_durable_before_optional_planning | `sha256:06506962eca91de88293b9baa7cc71061be62baf84fa50a58f1fe30332aef5cd` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0331` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_design_mount_failure_settles_both_concrete_items | `sha256:bb7677591f5e7d2b7ca017f2d6db17a9ced78263ff7ba6b2f907b30ae82bfe8c` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0332` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_fake_backend_persists_acceptance_before_poll_and_snapshots | `sha256:ab7b89b5c47ebd49b30c468174c097d01f9b993be78e917a2b42be5b5fb206d0` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0333` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_reserved_profile_seeds_concrete_plan_before_submission | `sha256:c070b135c4b627a8fac1a46e344b7e1b31545ec57ed2dcb79290e6c24c503aee` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0334` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_send_payload_carries_reserved_lifecycle_identity | `sha256:72c0a74224c96207c9283114da641f3fdb1e68fd268130f2f752a829a51409d8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0335` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_tracking_write_failure_cancels_and_fails_umbrella | `sha256:95eae751c81532fb64b6805449bc4d4994c4764929623b2ed551398e208ee5f1` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0336` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_unsubmitted_failure_is_persisted_before_branch_degrades | `sha256:b1f18471002fe378618f67fe2d4fbb440a7d1ea328605b4301bfb7ad23ca8ae6` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0337` | pylint | protected-access | structural | inline | symbol | tests/agents/test_design_helpers.py | test_get_compute_resource_protein_design_returns_medium | `sha256:8729d40cd01d205eaae4cc0dfb2f93c99107e88e02f8903d30d8510cfbf91ff1` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0338` | pylint | protected-access | structural | inline | symbol | tests/agents/test_design_helpers.py | test_get_compute_resource_unknown_falls_back_to_small | `sha256:5177408e990d2b34ce4af96964a892d42dabbae4ead41fb7555171dc41c82174` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0339` | pylint | protected-access | structural | inline | symbol | tests/agents/test_network_analyst_subgraph.py | test_dispatch_routes_through_subgraph_submit | `sha256:4e143b0eb1cebfdb4bc01c651f88b850cfe28250c01fbe1295ade6d161f3fbce` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0340` | pylint | protected-access | structural | inline | symbol | tests/agents/test_research_analyst_subgraph.py | test_submit_task_uses_subgraph | `sha256:439995578fb94e35076c30c4da8e4bcd0cdb2dc4061ab0f3519b93a4d46db0f0` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0341` | pylint | protected-access | structural | inline | symbol | tests/agents/test_review_add_query_failures.py | test_feedback_rag_records_failure_for_single_failed_query | `sha256:9e73231727fd2148bae7bdb9465a68c0d856c73e2fc5b0c4434882d6e7e5e304` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0342` | pylint | protected-access | structural | inline | symbol | tests/agents/test_review_add_query_failures.py | test_feedback_rag_records_failures_for_all_failed_queries | `sha256:d757d5771d1a696f29b2d2a7124d4f65f83881d5b898b6b91f9006292401b7e2` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0343` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_async_gc_keeps_the_request_loop_responsive | `sha256:7f35a982bdb094d7ec7b5a3d4b703863f4763b3f00cf7f53a6f20a5b862d8930` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0344` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_async_gc_reraises_unexpected_worker_failure | `sha256:7730531788cdeed258784f623dcfaab9c7c60b7b090a8e6a9f7489044aba5d96` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0345` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_gc_dependency_and_background_task_are_native_async | `sha256:a56b1e9b1d28f2f6ef41e27dea435ec61af8684205df4ccc5b6404085dd27e87` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0346` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_sync_write_routes_declare_gc_dependency | `sha256:aa43846d8f6ff46e20300d268aed091e8c2feb9cebc014c5a8f92c092d1512b8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0347` | pylint | too-many-arguments | temporary | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/dispatch.py | DeepGenomeDispatchMixin | `sha256:5718146c933edb576d220edaf4190c626b7966cf86d8097be352e92203b76c39` | bot-maintainers | 2026-07-19 | 2026-08-15 | 2026-08-31 | SAE-WORK-INITIAL-AUDIT | static-analysis-inventory |
| `SAE-TMP-0348` | pylint | C0103 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:09aee44e3ac01f4a545115d3328a6fb65f01c5c33e02dd0377e0039056d5484e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0349` | pylint | C0116 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:2f9a477a4c23b20217aab2ea3567537fbb4c460a226ddff45484eb4db2d1f337` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0350` | pylint | R0903 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:5c977671811194457cd243d6dd05218d1fc7f86882833161d8a50e2dfe5298f8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0351` | pylint | R0913 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:a15ec92995e4889b5cb4df0701e8c716d144aa145db9430f9e48cf5fb2f7d097` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0352` | pylint | R0917 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:8af6543ba16f67381086f0353049a789f5614276cc7434e402f6f4567c5c46d3` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0353` | pylint | W0613 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:f0fc55ced5b6b61c39a6343c9096d648e4ab54e91be08eb8ed93d449a49e297c` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0354` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/chat/service.py | run_phyto_chat_cached | `sha256:7c8abf6977bb26a0953727ea231b896bacab040602ba7337e7a037e5d24f7c0d` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/chat/service.py, static-analysis-inventory |
| `SAE-TMP-0355` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_post_bi_sql | `sha256:e9b02987d1e2de686402c3ea46f0b75ae6f86cf4a2b350217807b3727273b550` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0356` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_cached_gene_symbol_lookup | `sha256:063f0eeece2e09a0127065a7fb7888cd0028cf66cc935056a403369974743e09` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0357` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_cached_gene_annotation_lookup | `sha256:e7e9cea52b216d68a49a1cad774cd5d5e3a0b2f9e2b314b54c6436ed31eaf6a7` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0358` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/evolution/agent.py | find_spa_taxids | `sha256:9b1db3e2c21f23bbdfc39763a9fee97b55fb045dbc6abd84b744aa9df2be511f` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/evolution/agent.py, static-analysis-inventory |
| `SAE-TMP-0359` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/knowledge/retrieval.py | \_retrieve_scope_docs | `sha256:2d450f9fdf3c5d37449bc7d68f8e5757884d952f8eefc3677e0cb58736dd6063` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/knowledge/retrieval.py, static-analysis-inventory |
| `SAE-TMP-0360` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/knowledge/retrieval.py | \_rerank_batch | `sha256:4de11f90d993c77a4329ab5845fef2ae2a326f2617324c1929fed665b7389c9d` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/knowledge/retrieval.py, static-analysis-inventory |
| `SAE-TMP-0361` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/auth/iam.py | get_token | `sha256:86c7b9c1162e4bb19a32a5e178c0a2f362018486f6aa8c0efd145e60dafaa822` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/auth/iam.py, static-analysis-inventory |
| `SAE-TMP-0362` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/common/httpx_client.py | get_async_client | `sha256:d902f3079846e17beb3ccdcc68c0bd432c3e473f0171b0c54c975be37ee154c9` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/common/httpx_client.py, static-analysis-inventory |

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

### `SAE-TMP-0028`

Rationale:

```text
The Analyst package facade intentionally re-exports the public configuration and agent symbols independently from the implementation module. Repository owner approved this public API boundary on 2026-07-20.
```

Counterfactual:

```text
Reusing the implementation __all__ would make package import stability depend on internal exports and expose refactor drift.
```

Risk:

```text
Changing the facade can break downstream imports; public export changes require compatibility review.
```

### `SAE-TMP-0029`

Rationale:

```text
The Analyst agent and defaults modules intentionally maintain separate public export lists for runtime and configuration surfaces. Repository owner approved this public API boundary on 2026-07-20.
```

Counterfactual:

```text
Aliasing defaults.__all__ to agent.__all__ would couple independent configuration and runtime exports and make a local refactor change the other surface.
```

Risk:

```text
Changing either export list can break runtime or configuration imports; review the two public surfaces independently.
```

### `SAE-TMP-0040`

Rationale:

```text
Planning and submission wrappers share argument construction but intentionally keep independent deduplication, metadata, submit, and preset contracts. Repository owner approved this compatibility boundary on 2026-07-20.
```

Counterfactual:

```text
A broader helper would couple planning and submission compatibility wrappers and could erase their distinct deduplication or preset behavior.
```

Risk:

```text
Changing the shared argument projection can alter task submission semantics; preserve separate wrapper tests and review both contracts.
```

### `SAE-TMP-0041`

Rationale:

```text
Analyst and Data state TypedDicts intentionally repeat similarly named fields across independent graph contracts. Repository owner approved these separate state boundaries on 2026-07-20.
```

Counterfactual:

```text
Aliasing the state fields would couple Analyst and Data reducers and make an independent graph-state change cross the agent boundary.
```

Risk:

```text
State-key changes can break graph routing or persistence; update each contract with its owning graph tests.
```

### `SAE-TMP-0042`

Rationale:

```text
The DeepGenome mount test independently fixes the empty BriefGene annotation output shape at src/mcp_server_phytomni/agents/brief_gene/core.py:[119:124] versus tests/agents/test_deep_genome_brief_gene_mount.py:[234:239]. Repository owner approved this independent oracle on 2026-07-20.
```

Counterfactual:

```text
Replacing the fake with production seed data would let a mount defaulting regression update both implementation and expected value, hiding the failure.
```

Risk:

```text
Annotation defaults are consumed by the mounted graph; intentional field changes require mount-contract review.
```

### `SAE-TMP-0043`

Rationale:

```text
The knowledge-subgraph test independently fixes the BriefGene fan-out input state shape at src/mcp_server_phytomni/agents/brief_gene/core.py:[119:130] versus tests/agents/test_brief_gene_knowledge_subgraph.py:[81:89], instead of importing the production seed. Repository owner approved this state oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production seed would couple the expected state to implementation changes and stop the test from detecting retrieval-key drift.
```

Risk:

```text
State-shape changes can break fan-out routing; update the independent fixture only with graph-contract review.
```

### `SAE-TMP-0046`

Rationale:

```text
BriefGene and Review independently mount knowledge Send fan-out boundaries with different graph contracts. Repository owner approved this domain-specific topology boundary on 2026-07-20.
```

Counterfactual:

```text
A generic mount would hide BriefGene and Review graph topology, worker failure redaction, reducers, node names, and downstream edges.
```

Risk:

```text
A shared Send mount could route or redact the wrong domain state; preserve independent graph characterization tests.
```

### `SAE-TMP-0047`

Rationale:

```text
BriefGene and Review independently wire retrieve workers and reducers after their knowledge mounts. Repository owner approved this domain-specific topology boundary on 2026-07-20.
```

Counterfactual:

```text
A generic worker/reducer helper would obscure domain-specific node names, failure redaction, reducers, and graph edges.
```

Risk:

```text
Changing one fan-out contract could silently alter the other agent; keep graph-local tests and review topology changes independently.
```

### `SAE-TMP-0052`

Rationale:

```text
The DeepGenome mount test owns an isolated fake state schema for the mounted BriefGene subgraph. Repository owner approved this structural oracle on 2026-07-20.
```

Counterfactual:

```text
Reusing BriefGeneState would make the fake mutate with the implementation and allow a removed annotation field to pass the mount contract.
```

Risk:

```text
The fake must remain aligned with the mounted public state contract; changes require graph integration review.
```

### `SAE-TMP-0053`

Rationale:

```text
The mounted BriefGene fake independently fixes the preamble-section state shape used by the graph contract. Repository owner approved this structural oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production state would let a removed section or introduction field disappear from both fake and implementation without a failure.
```

Risk:

```text
Preamble fields are report-facing; intentional changes require mounted graph and contract review.
```

### `SAE-TMP-0054`

Rationale:

```text
The mounted BriefGene test independently fixes the nested annotation output fields expected from the subgraph. Repository owner approved this output oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production state fields would let an annotation rename update the expected projection and mask the mount regression.
```

Risk:

```text
Nested annotation fields are report-facing and require coordinated graph and fixture updates.
```

### `SAE-TMP-0055`

Rationale:

```text
The mounted BriefGene test independently fixes the preamble output fields expected from the subgraph. Repository owner approved this output oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production state would let a removed homology or section field mutate the expected output and hide the contract failure.
```

Risk:

```text
Preamble output changes affect report assembly; update the independent oracle only after contract review.
```

### `SAE-TMP-0056`

Rationale:

```text
The HTTP A2UI action test independently fixes the cancelled response shape and content. Repository owner approved this protocol oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production cancel payload would make a response-shape regression update both implementation and expected value, defeating the HTTP contract test.
```

Risk:

```text
Cancel semantics are consumer-visible; changes require coordinated A2UI contract review and golden updates.
```

### `SAE-TMP-0061`

Rationale:

```text
The work-item test independently enumerates the concrete DeepGenome job universe and section order. Repository owner approved this plan oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production work-item list would allow a removed or reordered job to pass because the expected list would mutate with it.
```

Risk:

```text
The explicit list must be updated with any intentional product-plan change and reviewed against report sections.
```

### `SAE-TMP-0062`

Rationale:

```text
Design and Research independently project interop task results into their domain states while sharing transport vocabulary. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A cross-domain result projection would couple Design and Research state schemas and could route input-required or failure outcomes incorrectly.
```

Risk:

```text
Interop status or task-id changes can affect pause/resume behavior; preserve both domain characterization suites.
```

### `SAE-TMP-0063`

Rationale:

```text
Design and Research independently submit interop tasks and record their own evidence updates. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A shared submit/evidence helper would couple capability names, state updates, and pause/resume payloads that are intentionally domain-specific.
```

Risk:

```text
A shared submit path could leak the wrong capability or telemetry into a domain state; retain separate interop tests.
```

### `SAE-TMP-0064`

Rationale:

```text
Design and Research independently encode interop degraded and evidence updates despite similar transport calls. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A cross-domain degraded-update helper would couple fallback and telemetry semantics that differ between Design and Research.
```

Risk:

```text
Degraded outcomes are user-visible; preserve domain-specific redaction and evidence assertions.
```

### `SAE-TMP-0065`

Rationale:

```text
Design and Research independently implement A2A resume and local-dispatch transitions with distinct state and protocol contracts. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A shared resume helper would couple A2A pause payloads, local fallback, and downstream dispatch behavior across the two agents.
```

Risk:

```text
Resume changes can lose user input or dispatch the wrong task; retain separate interop and domain workflow tests.
```

### `SAE-TMP-0066`

Rationale:

```text
Design and Research independently finalize interop evidence and completion telemetry for their own state machines. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A shared completion helper would hide differences in state keys, telemetry, and failure fallback between Design and Research.
```

Risk:

```text
Completion telemetry and degraded flags are part of the public task state; review each domain independently.
```

### `SAE-TMP-0067`

Rationale:

```text
Design and Research independently combine interop evidence with their domain-specific local dispatch workflows. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A shared orchestration helper would couple target IDs, capability names, state schemas, and fallback semantics across independent workflows.
```

Risk:

```text
Cross-domain orchestration changes can misroute analysis tasks; preserve separate graph and interop tests.
```

### `SAE-TMP-0068`

Rationale:

```text
Environment and Evolution share chat extraction mechanics but intentionally parse different domain outputs. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A shared response parser would couple Environment region output and Evolution taxid output and could accept the wrong domain shape.
```

Risk:

```text
Chat output parsing is domain-facing; preserve independent field validation and chat-subgraph tests.
```

### `SAE-TMP-0069`

Rationale:

```text
Environment and Evolution share low-level analyst request adapters but target different identifiers, configuration, and failure routes. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
A cross-domain dispatch helper would couple target IDs and error handling and could submit the wrong analyst task.
```

Risk:

```text
Dispatch changes can misroute or misreport analysis jobs; keep the two domain graph tests independent.
```

### `SAE-TMP-0071`

Rationale:

```text
Chat and Knowledge subgraphs intentionally retain parallel wrappers and routers with separate state keys and error defaults. Repository owner approved this boundary on 2026-07-20.
```

Counterfactual:

```text
Aliasing the chat and knowledge wrappers would couple independent router keys, defaults, and error semantics across public agent flows.
```

Risk:

```text
Subgraph wrapper changes can alter routing or fallback behavior; preserve separate wrapper and support tests.
```

### `SAE-TMP-0073`

Rationale:

```text
The relay package facade intentionally exposes a narrow public API independently from the audit implementation exports. Repository owner approved this security-facing boundary on 2026-07-20.
```

Counterfactual:

```text
Reusing the audit module __all__ would expose internal relay-audit symbols and make the public security boundary depend on implementation refactors.
```

Risk:

```text
Facade drift can expose or hide relay audit capabilities; review public exports with relay route tests.
```

### `SAE-TMP-0074`

Rationale:

```text
The relay audit test independently owns the credential-header denylist and verifies redaction behavior. Repository owner approved this security oracle on 2026-07-20.
```

Counterfactual:

```text
Sharing CREDENTIAL_HEADERS would make a removed secret header disappear from both implementation and expected set, masking credential leakage.
```

Risk:

```text
A stale denylist can either leak a new credential header or over-redact safe metadata; security review is required for changes.
```

### `SAE-TMP-0075`

Rationale:

```text
The configuration test independently enumerates the required deployment endpoint universe and its size. Repository owner approved this deployment oracle on 2026-07-20.
```

Counterfactual:

```text
Importing REQUIRED_DEPLOYMENT_FIELDS would allow an omitted or accidental endpoint to pass because production and expected values would change together.
```

Risk:

```text
The list is deployment-sensitive; intentional endpoint changes require synchronized environment, fixture, and documentation updates.
```

### `SAE-TMP-0076`

Rationale:

```text
The required endpoint test independently fixes the remaining operator and DeepGenome endpoint names. Repository owner approved this deployment oracle on 2026-07-20.
```

Counterfactual:

```text
Sharing the production tuple would let an omitted operator endpoint disappear from both the validator and its expected contract.
```

Risk:

```text
A stale expected tuple can block a legitimate deployment change; update it with the configuration contract and environment template.
```

### `SAE-TMP-0078`

Rationale:

```text
The MCP app and handler modules independently bind arguments for raw/enveloped dispatch and domain handler calls. Repository owner approved this compatibility boundary on 2026-07-20.
```

Counterfactual:

```text
Aliasing the argument projections would couple the raw/enveloped MCP seam to handler dispatch and make a local compatibility change cross the public boundary.
```

Risk:

```text
Argument binding changes can alter validation or response envelopes; preserve app and handler characterization tests.
```

### `SAE-TMP-0079`

Rationale:

```text
The response projection test independently fixes the canonical OpenAI field order and presence contract. Repository owner approved this protocol oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the production field tuple would allow a canonical field to be removed from the response and expected value simultaneously.
```

Risk:

```text
Consumers depend on canonical placement; any intentional field change requires compatibility review.
```

### `SAE-TMP-0083`

Rationale:

```text
The memory package facade intentionally re-exports policy constants independently from the memory model implementation. Repository owner approved this public boundary on 2026-07-20.
```

Counterfactual:

```text
Reusing models.__all__ would expose internal memory-model symbols and make the package API depend on implementation refactors.
```

Risk:

```text
Memory facade changes can break authenticated CRUD and prompt-recall consumers; preserve model and HTTP tests.
```

### `SAE-TMP-0084`

Rationale:

```text
The memory package facade intentionally re-exports public model types independently from the model module export list. Repository owner approved this public boundary on 2026-07-20.
```

Counterfactual:

```text
Aliasing the facade to models.__all__ would make internal model additions externally visible and couple the memory API to implementation changes.
```

Risk:

```text
Public memory type changes can break client imports and persistence contracts; review model and HTTP compatibility together.
```

### `SAE-TMP-0085`

Rationale:

```text
The memory package facade intentionally exposes accessor helpers independently from the runtime accessor implementation. Repository owner approved this runtime boundary on 2026-07-20.
```

Counterfactual:

```text
Reusing accessor.__all__ would expose internal storage seams and make the memory package API depend on runtime implementation refactors.
```

Risk:

```text
Accessor export changes can alter memory isolation or lifecycle entry points; preserve accessor and HTTP tests.
```

### `SAE-TMP-0086`

Rationale:

```text
The memory migration test independently fixes the durable column contract used to validate legacy upgrades. Repository owner approved this migration oracle on 2026-07-20.
```

Counterfactual:

```text
Importing the migration DDL would allow a missing column to pass schema and row-compatibility checks because implementation and expected schema would drift together.
```

Risk:

```text
A stale schema oracle can reject a planned migration; update it only alongside migration and rollback evidence.
```

### `SAE-TMP-0087`

Rationale:

```text
Analyst and review knowledge graph fixture shapes remain intentionally duplicated across graph-I/O and fan-out characterization tests.
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
==tests.agents.test_brief_gene_chat_subgraph:[71:79]
==tests.agents.test_brief_gene_knowledge_subgraph:[66:74]
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
==tests.agents.test_brief_gene_knowledge_subgraph:[81:86]
==tests.agents.test_deep_genome_brief_gene_mount:[334:339]
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

### `SAE-TMP-0100`

Rationale:

```text
DeepGenome routing tests intentionally share concrete work-item fixtures with report tests.
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
DeepGenome routing tests intentionally share concrete work-item fixtures with report tests.
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
DeepGenome routing tests intentionally share concrete work-item fixtures with report tests.
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
==tests.agents.test_environment_analyst_subgraph:[85:97]
    monkeypatch.setattr(
        environment_graph, "submit_analyst_via_subgraph", subgraph_mock
    )
    # _build_submit_agent must be stubbed because constructing a real
    # AnalystAgent reaches into cached-agent registry + IAM token
    # acquisition, neither of which is available offline.
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
==tests.agents.test_graphs_manifest_export:[179:194]
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

### `SAE-TMP-0141`

Rationale:

```text
Similar lines in 2 files
==test_api_chat_completions:[270:276]
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
==test_api_chat_completions:[270:277]
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
==test_api_chat_completions:[273:280]
==tests.agents.test_chat_agent:[461:468]
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
==test_api_chat_completions:[275:287]
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
==test_api_runs_list:[550:559]
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
Task-status and lifecycle tests intentionally share the same durable transition fixture.
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
==test_result_formatting_metadata_contract:[33:42]
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
==test_resume_mcp:[104:114]
==test_stdio_progress:[49:60]
    monkeypatch.setattr(
        app_mod,
        "_graph_stream_target",
        lambda _tool, _args: (fake_app, {"user_query": "q"}),
    )
    monkeypatch.setattr(
        app_mod, "_astream_progress_ticks", _fake_astream_progress
    )
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
==test_polling:[64:72]
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
==test_task_reconcile:[104:111]
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
DeepGenome routing tests intentionally share concrete work-item fixtures with report tests.
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
==test_deep_genome_store:[39:50]
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
==test_deep_genome_store:[52:65]
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
==tests.agents.test_stream_graph_agent:[426:433]
    for forbidden in (
        "bearer-secret",
        "postgresql://",
        "db-user:db-password",
        "SELECT secret_token",
    ):
        assert forbidden not in evidence


async def test_unexpected_error_logs_location_without_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected logs include identity/location but not raw exception text."""
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

### `SAE-TMP-0200`

Rationale:

```text
The fake deliberately models one OBS lifecycle with class-level objects, pagination, captured SDK calls, and dynamic camelCase dispatch; replacing it with a value-only fake would lose the shared external-state contract.
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
The polling client owns a response iterator and request-path capture so the test proves sequential HTTP status/JSON handling and monotonic revision collection.
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
The fake MCP server retains constructor instances, tool catalogs, and requested server names across discovery and invocation to prove the external lifecycle boundary.
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
The upload fake models a one-shot mutable chunk stream and records read offsets so the byte-budget guard can prove it stops at the first overflow.
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
The fake must accept the SDK constructor and dynamically synthesize arbitrary camelCase methods that raise a sentinel, proving storage sanitization preserves the cause without leaking its text.
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
Review A2UI authoring is best effort; malformed or unexpected projection inputs must fall back to the raw interrupt so the graph pause and resume contract remains usable.
```

Counterfactual:

```text
Removing the catch would let authoring failures break Review pause/resume; narrowing it to currently observed exception classes would leave unknown authoring failures unhandled.
```

Risk:

```text
A broad catch can hide a projection regression; the fallback emits a class-only log and the A2UI HTTP tests cover the enabled and fallback paths.
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
_capture_create_payload isolates an analyst graph payload/path seam so its private invariant is asserted without invoking the full graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0245`

Rationale:

```text
test_dispatch_chat_uses_subgraph isolates a DeepGenome report adapter seam so the mounted subgraph contract is asserted without unrelated stages.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0246`

Rationale:

```text
test_deep_genome_generic_dispatch_is_submit_only pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0247`

Rationale:

```text
test_dispatch_knowledge_uses_subgraph isolates a DeepGenome report adapter seam so the mounted subgraph contract is asserted without unrelated stages.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0248`

Rationale:

```text
test_all_optional_failures_preserve_profile_and_fail_owner isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0250`

Rationale:

```text
test_analysis_prompt_parts_rejects_unknown_type pins a design resource or prompt guard at its smallest decision seam rather than through a full graph invocation.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0251`

Rationale:

```text
test_dispatch_request_carries_to_id_as_target isolates network analyst dispatch so target mapping and subgraph routing remain explicit.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0252`

Rationale:

```text
test_submit_task_propagates_failed_status isolates research analyst dispatch so routing and failure propagation are checked without a remote task.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0253`

Rationale:

```text
test_extract_goals_uses_chat_subgraph isolates the research chat goal-parser seam so the compiled chat adapter contract is asserted directly.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0255`

Rationale:

```text
test_feedback_rag_failures_empty_when_no_add_queries isolates review supplementary-query failure accumulation so each degraded record remains attributable to its query.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0261`

Rationale:

```text
test_async_gc_coalesces_concurrent_passes isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0262`

Rationale:

```text
test_progress_forwarded_when_token_present isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
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
The lifecycle test keeps its coordinator setup in one scenario so persistence and failure ordering remain readable.
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
Async GC waits for a worker-thread Event; polling keeps the request loop responsive without a thread-safe callback.
```

Counterfactual:

```text
Replace worker completion polling with an equivalent non-blocking completion primitive before removing this directive.
```

Risk:

```text
Suppression can hide a future regression in async GC completion handling.
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

### `SAE-TMP-0315`

Rationale:

```text
test_submit_output_dir_forwards_input_fingerprint isolates an analyst graph payload/path seam so its private invariant is asserted without invoking the full graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0316`

Rationale:

```text
test_dispatch_coordinator_receives_effective_poll_id pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0317`

Rationale:

```text
test_non_transferred_type_routes_subgraph pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0318`

Rationale:

```text
test_prepare_tasks_includes_protein_structure pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0319`

Rationale:

```text
test_promoter_routes_to_wrapper pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0320`

Rationale:

```text
test_protein_structure_routes_to_wrapper pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0321`

Rationale:

```text
test_route_after_brief_gene_reaches_preparation_only_when_enabled pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0322`

Rationale:

```text
test_route_analyst_tasks_sends_design_to_design_node pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0323`

Rationale:

```text
test_route_analyst_tasks_sends_each_generic_to_its_own_node pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0324`

Rationale:

```text
test_route_analyst_tasks_sends_evolution_to_evolution_node pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0325`

Rationale:

```text
test_route_experiment_skips_protocol_when_analyst_disabled pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0326`

Rationale:

```text
test_route_start_waits_for_brief_gene_before_task_preparation pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0327`

Rationale:

```text
test_route_synthesize_preserves_skip_fixture pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0328`

Rationale:

```text
test_route_synthesize_rejects_all_terminal_failures pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0329`

Rationale:

```text
test_route_synthesize_waits_for_every_concrete_work_item pins a DeepGenome routing or submit branch at the smallest coordinator seam without running the full report graph.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0330`

Rationale:

```text
test_brief_gene_success_is_durable_before_optional_planning isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0331`

Rationale:

```text
test_design_mount_failure_settles_both_concrete_items isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0332`

Rationale:

```text
test_fake_backend_persists_acceptance_before_poll_and_snapshots isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0333`

Rationale:

```text
test_reserved_profile_seeds_concrete_plan_before_submission isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0334`

Rationale:

```text
test_send_payload_carries_reserved_lifecycle_identity isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0335`

Rationale:

```text
test_tracking_write_failure_cancels_and_fails_umbrella isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0336`

Rationale:

```text
test_unsubmitted_failure_is_persisted_before_branch_degrades isolates a DeepGenome coordinator/report lifecycle transition so persistence order and failure settlement stay observable without remote analysis execution.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0337`

Rationale:

```text
test_get_compute_resource_protein_design_returns_medium pins a design resource or prompt guard at its smallest decision seam rather than through a full graph invocation.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0338`

Rationale:

```text
test_get_compute_resource_unknown_falls_back_to_small pins a design resource or prompt guard at its smallest decision seam rather than through a full graph invocation.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0339`

Rationale:

```text
test_dispatch_routes_through_subgraph_submit isolates network analyst dispatch so target mapping and subgraph routing remain explicit.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0340`

Rationale:

```text
test_submit_task_uses_subgraph isolates research analyst dispatch so routing and failure propagation are checked without a remote task.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0341`

Rationale:

```text
test_feedback_rag_records_failure_for_single_failed_query isolates review supplementary-query failure accumulation so each degraded record remains attributable to its query.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0342`

Rationale:

```text
test_feedback_rag_records_failures_for_all_failed_queries isolates review supplementary-query failure accumulation so each degraded record remains attributable to its query.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0343`

Rationale:

```text
test_async_gc_keeps_the_request_loop_responsive isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0344`

Rationale:

```text
test_async_gc_reraises_unexpected_worker_failure isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0345`

Rationale:

```text
test_gc_dependency_and_background_task_are_native_async isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0346`

Rationale:

```text
test_sync_write_routes_declare_gc_dependency isolates module-private transport wiring so dependency, worker, or notification behavior is asserted without a live HTTP or MCP transport.
```

Counterfactual:

```text
Use a public graph or transport seam only when it preserves this branch-level assertion without remote side effects.
```

Risk:

```text
A protected-access exception can hide unintended coupling or stale private invariants.
```

### `SAE-TMP-0347`

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

### `SAE-TMP-0348`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0349`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0350`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0351`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0352`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0353`

Rationale:

```text
pylint: disable=C0103,C0116,R0903,R0913,R0917,W0613
```

Counterfactual:

```text
Keep the OBS SDK names and signatures in a file-local stub mask while validating the stub with mypy, pyright, and Ruff.
```

Risk:

```text
A broader directive could hide a real stub-shape regression if the mask is expanded beyond the six recorded rules.
```

### `SAE-TMP-0354`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0355`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0356`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0357`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0358`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0359`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0360`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0361`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```

### `SAE-TMP-0362`

Rationale:

```text
noqa: ASYNC109
```

Counterfactual:

```text
Keep the timeout as a downstream transport budget; introduce a local cancellation scope only if this function starts owning request lifetime.
```

Risk:

```text
The exemption could hide an accidental timeout that is not forwarded to the underlying client.
```
