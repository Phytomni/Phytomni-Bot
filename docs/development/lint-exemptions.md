# Static-analysis exemption ledger

This file is generated from `static-analysis-exemptions.toml`.

Regeneration:

```bash
uv run python scripts/check_static_analysis_exemptions.py render-docs
```

- Schema version: `1`
- Policy default: `deny`
- Authorized records: `104`

## Informational counts

| Tool and rule                                     | Records |
| ------------------------------------------------- | ------: |
| `flake8:E203`                                     |       1 |
| `flake8:W503`                                     |       1 |
| `mypy:misc`                                       |       1 |
| `mypy:prop-decorator`                             |       1 |
| `pylint:C0103`                                    |       1 |
| `pylint:C0116`                                    |       1 |
| `pylint:R0801`                                    |      22 |
| `pylint:R0903`                                    |       6 |
| `pylint:R0913`                                    |       1 |
| `pylint:R0917`                                    |       1 |
| `pylint:W0613`                                    |       1 |
| `pylint:broad-exception-caught`                   |       2 |
| `pylint:contextmanager-generator-missing-cleanup` |       2 |
| `pylint:path-ignore`                              |       4 |
| `pylint:protected-access`                         |      39 |
| `pylint:too-few-public-methods`                   |       3 |
| `pylint:wrong-import-position`                    |       1 |
| `pymarkdown:md013`                                |       1 |
| `pytest:error`                                    |       1 |
| `ruff:ASYNC109`                                   |       6 |
| `ruff:ASYNC110`                                   |       3 |
| `ruff:E402`                                       |       2 |
| `ruff:N802`                                       |       1 |
| `ruff:N803`                                       |       1 |
| `ruff:N815`                                       |       1 |

## Exact records

| ID | Tool | Rule | Classification | Mechanism | Target | Path | Symbol | Fingerprint | Owner | Introduced | Review | Expiry | Remediation | Tests |
| \--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `SAE-STR-0001` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | DeepGenomeProfileMixin | `sha256:f919078a50df4db511f41d14935848003d67ff934117be82f1f237cc9b4cf6f4` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_cache_candidates.py, tests/agents/test_deep_genome_sql.py |
| `SAE-STR-0002` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/report.py | DeepGenomeReportMixin | `sha256:56148d2ccbbd5b3a0eb2b611920c543f2d0c083274ba1a27a592fda6815b9978` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_deep_genome_chat_subgraph.py, tests/agents/test_deep_genome_knowledge_subgraph.py, tests/agents/test_deep_genome_lifecycle.py, tests/agents/test_deep_genome_report.py |
| `SAE-STR-0003` | pylint | too-few-public-methods | structural | inline | symbol | src/mcp_server_phytomni/agents/review/report.py | ReviewReportMixin | `sha256:1437a6aeed48bfca0a47bfe423e9f70a2258c20f3ae7e558805dfc7cc19af8a8` | bot-maintainers | 2026-07-18 | 2027-01-18 | — | — | tests/agents/test_review_add_query_failures.py, tests/agents/test_review_report_helpers.py, tests/agents/test_review_revised_fan_out.py |
| `SAE-TMP-0001` | flake8 | E203 | structural | config | config | .flake8 | [flake8].extend-ignore | `sha256:ee3ff2f285462681d7e4dd5eed33cbf4cd5ef1d73ca986281e086dc5ef2457a8` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0002` | flake8 | W503 | structural | config | config | .flake8 | [flake8].extend-ignore | `sha256:1d93293eb8e91ba241c13a2db0c100dc24b5305c4b0f429faa5ca2f334bfccc1` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0009` | mypy | misc | structural | inline | symbol | tests/unit/interop/test_capabilities.py | test_capability_is_frozen_and_qualified_name_is_canonical | `sha256:50c5686aeb514f0ea385b0f5efcb20543fb02b479f0a05b4b32116c2e38f65fe` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0011` | mypy | prop-decorator | structural | inline | symbol | src/mcp_server_phytomni/api/schemas.py | FileUploadResponse | `sha256:243d7b24f3e529af946567903305f2f5f09ad24b6316e1063a7ed27a93107a6c` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0028` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/__init__.py | 42:47 | `sha256:04b1f52c7567afcb17be02df79c0cadd82b90a00fa8a07e06ab48b5a2dafafb8` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_nodes.py, tests/agents/test_analyst_routers.py, static-analysis-inventory |
| `SAE-TMP-0029` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/agent.py | 39:44 | `sha256:db1a39a7296629412f62345cacd4a0f73d49c06d2acf3b459fe10a81dcf460dc` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_nodes.py, tests/agents/test_analyst_routers.py, static-analysis-inventory |
| `SAE-TMP-0040` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/planning.py | 102:108 | `sha256:9b90c01740a4af886dc8cfc1e1a3e7c64217106cab5794e1f56231e30846f403` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_planning_helpers.py, tests/agents/test_analyst_submission_helpers.py, static-analysis-inventory |
| `SAE-TMP-0041` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/analyst/state.py | 118:123 | `sha256:21774e4fe0ecfadbdc6dd555adeb868ab6586100fe80e17c6132e61a7c56331d` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_analyst_graph_io.py, tests/agents/test_data_chat_subgraph.py, tests/agents/test_data_knowledge_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0046` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 81:87 | `sha256:f91cdb24c30953e12e83ae676f975ae384fb5282bd6a09291762361917065bff` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_brief_gene_knowledge_subgraph.py, tests/agents/test_review_retrieve_fan_out.py, static-analysis-inventory |
| `SAE-TMP-0047` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/brief_gene/graph_knowledge_subgraph.py | 88:95 | `sha256:7934e0cbbb6fcb7e9f9c798b7ff2d84893e1ef5810e802ff6f6acfb19356eb05` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_brief_gene_knowledge_subgraph.py, tests/agents/test_review_retrieve_fan_out.py, static-analysis-inventory |
| `SAE-TMP-0062` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 459:466 | `sha256:2bfc4c674487ec3709c9416f1ce00a1d91d7adea8b04bda6af27ca95bb901a9e` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0063` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 589:601 | `sha256:380e770ba8e686a6dd137d0f6b75039e0bd24da412d62e780308af6c1cd966ab` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0064` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 660:674 | `sha256:17be830a7f55f5b3a5335553c9a8e2bd283d2cfaa4e64eb0abe45b8fff10f692` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0065` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 667:679 | `sha256:03418835fd860f351014eb1e4644c25250b07235890795a172659d6f4e2f83bb` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0066` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/design/agent.py | 771:801 | `sha256:a12cebe8c905e0486c0e550f549236dd16f342d51bef2385d00c5474b7dcd76d` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_design_interop.py, tests/agents/test_research_interop.py, static-analysis-inventory |
| `SAE-TMP-0068` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/environment/agent.py | 91:98 | `sha256:1d2314ab58ae61d77b3cdf87fb285fda0f3f68578e52c9798527027118c37cc6` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_environment_chat_subgraph.py, tests/agents/test_evolution_chat_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0069` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/environment/graph.py | 183:189 | `sha256:498ba1c179fd8527195e76108ad2d23523e8a4b508958952439fbef5f7dc0d2c` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_environment_analyst_subgraph.py, tests/agents/test_evolution_analyst_subgraph.py, static-analysis-inventory |
| `SAE-TMP-0071` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/agents/shared/chat_subgraph.py | 101:142 | `sha256:e6c553359093f37502c15611b3d82295a314c4d2579ff20ffb23bab3a54980de` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/agents/test_chat_subgraph_wrapper.py, tests/agents/test_knowledge_subgraph_support.py, static-analysis-inventory |
| `SAE-TMP-0073` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/api/relay/__init__.py | 20:25 | `sha256:11110a31a61699cdd16bc8f75aa9c481305c2dad14bf2bb7471c6c45723cd27a` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/server/test_relay_mount.py, tests/server/test_relay_audit_routes.py, static-analysis-inventory |
| `SAE-TMP-0074` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/api/relay/audit_filter.py | 27:38 | `sha256:e403f78e603f501a9592111cdd3963a7670f069c42cb5d1e1755b764d1dc32e9` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_relay_audit_filter.py, static-analysis-inventory |
| `SAE-TMP-0075` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 16:28 | `sha256:0a106c71d1e188f33ba116a1a15980da2ccbd8924042f13b31d0dd5df3f2585c` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/config/test_required_env.py, tests/unit/test_defaults.py, static-analysis-inventory |
| `SAE-TMP-0076` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/config/required_env.py | 41:46 | `sha256:b181294b667a5a5e3b3cc601d177d1d5e50806e12df472c917d370454afa17f9` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/config/test_required_env.py, static-analysis-inventory |
| `SAE-TMP-0083` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 45:52 | `sha256:781cbd4798bbd051136fe6306e69070b99da317486e9f110b66c4c53f764ebd8` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_models.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0084` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 59:64 | `sha256:dab91daad1dc692da89b2d3c4bedfcd081201a6cd5e592f6d9f289f0ad825f66` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_models.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0085` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/__init__.py | 73:79 | `sha256:02a0ef0f7f5041c275b28c1c4c24c010eae62e1de902e15478e70af3b9aa9b42` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_accessor.py, tests/server/test_memory_http.py, static-analysis-inventory |
| `SAE-TMP-0086` | pylint | R0801 | structural | diagnostic | pair | src/mcp_server_phytomni/runtime/memory/migrations.py | 23:31 | `sha256:4f06fd5ec9e45a0c63ea3f65946f56998710e5123f0135fc1bc20ca49b9bbe3a` | bot-maintainers | 2026-07-20 | 2027-01-17 | — | — | tests/unit/test_memory_migrations.py, static-analysis-inventory |
| `SAE-TMP-0194` | pylint | R0903 | structural | diagnostic | symbol | tests/agents/test_deep_genome_submit.py | \_FakeApp | `sha256:1f7f51708ece122db06fccc8c565c771ccda09fd3dab04360277d25248e3fef0` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0200` | pylint | R0903 | structural | diagnostic | symbol | tests/conftest.py | \_build_fake_obs_client.\_FakeObsClient | `sha256:2dc747295b1e436a070df77a883fd813565b56d9c86de87801b81cfbb178fae7` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0211` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/e2e/test_polling.py | test_http_poll_records_distinct_monotonic_revisions.Client | `sha256:f4a6bb85b202f6f9be1a4bdf895fd80b219a442eab4fd5f04fb29cd46b4c3d93` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0213` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/interop/test_fake_peer_e2e.py | \_FakeMCPServer | `sha256:0dcf2d975d525a931a08dd914e21c20836ea700969650768d93515b034f55122` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0214` | pylint | R0903 | structural | diagnostic | symbol | tests/unit/test_api_file_upload.py | \_ChunkedUpload | `sha256:eda00c1f02071e7fa974cdef673f09b7b2efc3b1302890c475dbc5ed46cefe1d` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0222` | pylint | broad-exception-caught | structural | inline | symbol | e2e/helpers/polling.py | \_reconciled_task_state | `sha256:58b3b2f3bb4ee5b58ee8ca5380146a1ab5d43c18bff5e36510cb912eece854a3` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0223` | pylint | broad-exception-caught | structural | inline | symbol | src/mcp_server_phytomni/api/a2ui_projection.py | project_review_interrupt | `sha256:213d3fed9bbab6b605c38b207f9117273e583e0703bbf78213b08a0dbf2e8f6d` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | tests/server/test_a2ui_review_http.py, tests/server/test_a2ui_runtime.py, static-analysis-inventory |
| `SAE-TMP-0228` | pylint | contextmanager-generator-missing-cleanup | structural | inline | span | tests/agents/test_cache_candidates.py | — | `sha256:1a93e1b05a8cfa872aeddc39c7850189f9089c16b59a0d36c97909cc4341ae03` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0229` | pylint | contextmanager-generator-missing-cleanup | structural | inline | span | tests/conftest.py | — | `sha256:8051cd3af754f03cba8b655218a54168e8867d393d6ed998ce98de611d0b7b9a` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0236` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:38fd2748eed7f3803e3176f0ef992ae35b8441cfc99a8e873d01821f62c700eb` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0237` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:3e1bc269c16210ee4149f6c708497a514291d2716f41ad7186c731fe6fce7ea3` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0238` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:829ec7992def978f01ad8efae0a0ee64c5e1767cfeb76f3a13236dc4ed5cef23` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0239` | pylint | path-ignore | structural | config | config | pyproject.toml | tool.pylint.main.ignore | `sha256:b7c41d52ad3e667583ed5bc27c3cffee33f6c621371befb228c17357718fc94a` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0241` | pylint | protected-access | structural | inline | symbol | tests/agents/test_analyst_graph_nodes.py | \_capture_create_payload | `sha256:cd6697f92e2f53b8be8ff1d45348694b78b636827525788f5ca1dab931844253` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0245` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_chat_subgraph.py | test_dispatch_chat_uses_subgraph | `sha256:dc8cf6a9d64081107dcb8c0509cf9b38ffc98a1931b77e52d26b7fee2d4383eb` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0246` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_deep_genome_generic_dispatch_is_submit_only | `sha256:d7231430fe9c4901f96ab215506cd45a2986336a0c009b0bf3ebb77dc60d81da` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0247` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_knowledge_subgraph.py | test_dispatch_knowledge_uses_subgraph | `sha256:e738b7820e753539f447e164a30d465df7abba5f27a3730d42851135fa2b38aa` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0250` | pylint | protected-access | structural | inline | symbol | tests/agents/test_design_helpers.py | test_analysis_prompt_parts_rejects_unknown_type | `sha256:5920289e37592f4bf00a38fa591f263313e4ad7774c9a368f018f6090560b37c` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0251` | pylint | protected-access | structural | inline | symbol | tests/agents/test_network_analyst_subgraph.py | test_dispatch_request_carries_to_id_as_target | `sha256:7b00f3c4d7abe53a1e197b9f01f9b447860703561be848f801fd9566b1be0395` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0252` | pylint | protected-access | structural | inline | symbol | tests/agents/test_research_analyst_subgraph.py | test_submit_task_propagates_failed_status | `sha256:703861b6c2122568233a161f3323b921ed51aa617a31c181e20085e857a2f98e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0253` | pylint | protected-access | structural | inline | symbol | tests/agents/test_research_chat_subgraph.py | test_extract_goals_uses_chat_subgraph | `sha256:e779e814ad5960cc1e61077d79408530084759acc64e8a04347db5900bf0a743` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0255` | pylint | protected-access | structural | inline | symbol | tests/agents/test_review_add_query_failures.py | test_feedback_rag_failures_empty_when_no_add_queries | `sha256:ea1bd79bc055e411a3e1bb27595e369d13a9e6be0644e57bb7e949128bb161b6` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0261` | pylint | protected-access | structural | inline | symbol | tests/server/test_run_gc_background.py | test_async_gc_coalesces_concurrent_passes | `sha256:0dcac7edf70b73e87d365ab2a55a6988025320b3309f742972b4e93446d03736` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0293` | pylint | wrong-import-position | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:637afabc246f45d9145f3aa93a39fe94d65fb830b2067196e8f33d8b77ed1847` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0294` | pymarkdown | md013 | structural | config | config | pyproject.toml | tool.pymarkdown.plugins.md013.enabled | `sha256:34d8c475a236a211d36db927ee2de462f03fdb4f3f07864359540255c750bf01` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0295` | pytest | error | structural | config | config | pyproject.toml | tool.pytest.ini_options.filterwarnings[0] | `sha256:0aa661937816a1fb17f333eb9a372e15f3093ed7bf8b75bc5d904d38422ef20f` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0300` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/report.py | \_write_async | `sha256:a7cb1e8424467f0727f7a3b99bf30356d9bb18b99462d892fbf92402f43dd198` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0301` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/api/run_lifecycle.py | purge_expired_runs_best_effort_async | `sha256:d9bfcdb38455dce2970fd011c237a1131de5632e63d545189a9d87480a21b017` | bot-maintainers | 2026-07-20 | 2027-01-20 | — | — | tests/server/test_run_gc_background.py |
| `SAE-TMP-0302` | ruff | ASYNC110 | structural | inline | symbol | src/mcp_server_phytomni/api/relay/obs.py | \_wait_obs_future | `sha256:a8f3e3adef4dab97a6423e96bc1f91159c519a5060e52a58156302de97e99e33` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0310` | ruff | E402 | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:5bb9d315b606c4e79f7f08a5a3896da1ae9fe6ea43c2d6d2055599a1c4e33f40` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0311` | ruff | E402 | structural | inline | span | scripts/\_visualize_bootstrap.py | — | `sha256:9e583e7c25de4e2c0f6b97674641e1e427cb71f230c2aa96fb00b4aa48ddf6cf` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0312` | ruff | N802 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/\*.pyi | `sha256:bf0326cb042985ec93478cbec49d2e724864485a45acf6ad1e15bc9d520eb9a4` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0313` | ruff | N803 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/**/*.pyi | `sha256:04fd5950474f7a9f1e297946d6fbe12928593497186dbe231583d79859f82d30` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0314` | ruff | N815 | structural | config | config | pyproject.toml | tool.ruff.lint.per-file-ignores.typings/\*\*/*.pyi | `sha256:c46f9c1d9fbddac831ac4f216f43c4ff6703ad4274821d10b4f0186703a912e0` | bot-maintainers | 2026-07-17 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0315` | pylint | protected-access | structural | inline | symbol | tests/agents/test_analyst_graph_nodes.py | test_submit_output_dir_forwards_input_fingerprint | `sha256:854cb4451cc07c0b5a5399e4c782adb77e2a6ce896ed0e39709ba05851d78eaa` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0316` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_dispatch_coordinator_receives_effective_poll_id | `sha256:1c42ef10ae7bdea7aa74fa1ae078e594510e910150e967a6338e882ccf466278` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
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
| `SAE-TMP-0328` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_synthesize_rejects_all_terminal_failures | `sha256:1c5dd8189d6a4d1ec4e26252e089d222cd7059fe5bd79b3ba64f4e1b38293db8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0329` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_dispatch_routing.py | test_route_synthesize_waits_for_every_concrete_work_item | `sha256:96e07ad2d7daf4e8e9186fa1e14673d96fb80a59ffc8dcde07cedd2ba8730ca0` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0330` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_brief_gene_success_is_durable_before_optional_planning | `sha256:06506962eca91de88293b9baa7cc71061be62baf84fa50a58f1fe30332aef5cd` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0333` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_reserved_profile_seeds_concrete_plan_before_submission | `sha256:c070b135c4b627a8fac1a46e344b7e1b31545ec57ed2dcb79290e6c24c503aee` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0334` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_send_payload_carries_reserved_lifecycle_identity | `sha256:72c0a74224c96207c9283114da641f3fdb1e68fd268130f2f752a829a51409d8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
| `SAE-TMP-0335` | pylint | protected-access | structural | inline | symbol | tests/agents/test_deep_genome_lifecycle.py | test_tracking_write_failure_cancels_and_fails_umbrella | `sha256:95eae751c81532fb64b6805449bc4d4994c4764929623b2ed551398e208ee5f1` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | static-analysis-inventory |
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
| `SAE-TMP-0348` | pylint | C0103 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:09aee44e3ac01f4a545115d3328a6fb65f01c5c33e02dd0377e0039056d5484e` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0349` | pylint | C0116 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:2f9a477a4c23b20217aab2ea3567537fbb4c460a226ddff45484eb4db2d1f337` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0350` | pylint | R0903 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:5c977671811194457cd243d6dd05218d1fc7f86882833161d8a50e2dfe5298f8` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0351` | pylint | R0913 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:a15ec92995e4889b5cb4df0701e8c716d144aa145db9430f9e48cf5fb2f7d097` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0352` | pylint | R0917 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:8af6543ba16f67381086f0353049a789f5614276cc7434e402f6f4567c5c46d3` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0353` | pylint | W0613 | structural | inline | span | typings/obs/__init__.pyi | — | `sha256:f0fc55ced5b6b61c39a6343c9096d648e4ab54e91be08eb8ed93d449a49e297c` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | typings/obs/__init__.pyi, tests/unit/test_style_naming.py, static-analysis-inventory |
| `SAE-TMP-0355` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_post_bi_sql | `sha256:e9b02987d1e2de686402c3ea46f0b75ae6f86cf4a2b350217807b3727273b550` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0356` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_cached_gene_symbol_lookup | `sha256:063f0eeece2e09a0127065a7fb7888cd0028cf66cc935056a403369974743e09` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0357` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/deep_genome/profile.py | \_cached_gene_annotation_lookup | `sha256:e7e9cea52b216d68a49a1cad774cd5d5e3a0b2f9e2b314b54c6436ed31eaf6a7` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/deep_genome/profile.py, static-analysis-inventory |
| `SAE-TMP-0358` | ruff | ASYNC109 | structural | inline | symbol | src/mcp_server_phytomni/agents/evolution/agent.py | find_spa_taxids | `sha256:9b1db3e2c21f23bbdfc39763a9fee97b55fb045dbc6abd84b744aa9df2be511f` | bot-maintainers | 2026-07-19 | 2027-01-17 | — | — | src/mcp_server_phytomni/agents/evolution/agent.py, static-analysis-inventory |
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
