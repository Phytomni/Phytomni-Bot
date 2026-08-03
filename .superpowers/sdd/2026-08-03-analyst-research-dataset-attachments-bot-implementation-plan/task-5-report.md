# Task 5 Implementation Report

## Status

DONE_WITH_CONCERNS.

Task 5 implementation is present and the focused lifecycle regression suite is
green. The required writable-cache `make scoped` was run to terminal completion
and failed only in the final static-analysis exemption review with existing
duplicate-code fingerprints that include out-of-scope files.

## Files Changed

- `src/mcp_server_phytomni/api/schemas.py`
- `src/mcp_server_phytomni/api/routes/attachment_inputs.py`
- `src/mcp_server_phytomni/api/routes/context_types.py`
- `src/mcp_server_phytomni/api/routes/agents.py`
- `src/mcp_server_phytomni/api/agent_runs.py`
- `src/mcp_server_phytomni/api/factory.py`
- `tests/server/test_api_agent_runs.py`
- `tests/server/test_api_agent_context_runs.py`
- `tests/server/test_api_app_compatibility.py`
- `.superpowers/sdd/2026-08-03-analyst-research-dataset-attachments-bot-implementation-plan/task-5-report.md`

No changes were made to the forbidden interface files:

- `src/mcp_server_phytomni/mcp/schemas.py`
- `src/mcp_server_phytomni/agents/shared/query_resolution.py`
- `src/mcp_server_phytomni/agents/shared/options.py`

## Implementation

- Added native HTTP request fields for attachment owner assertion and bounded
  dataset descriptions, while leaving public MCP schemas unchanged.
- Added owner resolution for opaque HTTP attachments:
  `owner_subject` is treated as an assertion, explicit assertions require
  `files:delegate`, and omitted assertions use `principal.user_id`.
- Added request-local native attachment resolution and preparation:
  managed documents are projected to `obs_file_list`, managed datasets are
  projected to `data_list`, dataset descriptions are completed only where the
  native Analyst/Research lifecycle needs them, and validation runs before 202
  acceptance.
- Added safe native request snapshots for ordinary and context runs. The
  persisted request JSON contains only bounded metadata (`dialogue_id`,
  `locale`, `route`) and does not serialize attachment IDs, owner assertions,
  dataset descriptions, raw paths, prompts, or private evidence.
- Extended `ContextAgentRequest` with private dataclass fields for resolved
  attachments, dataset description, and managed evidence. Context replay reuses
  the staged result and does not call the dataset-description provider again.
- Passed `ManagedAttachmentEvidence` through only request-local preparation and
  detached execution closures. It is not included in Agent args, request JSON,
  registry rows, context serialization, or response DTOs.
- Preserved detached submission safety by keeping reservations query/request
  JSON free and redacting managed attachment values before background result
  persistence/projection.
- Preserved native HTTP 200/202 behavior and did not broaden dataset support to
  chat completions.
- Updated the factory compatibility adapter and tests for the new private
  `attachment_evidence` invoke parameter.

## Tests And Commands

Initial red run:

```text
UV_CACHE_DIR=/tmp/phytomni-bot-dataset-attachments UV_NO_SYNC=1 uv run pytest tests/server/test_api_agent_runs.py tests/server/test_api_agent_context_runs.py tests/server/test_api_app_compatibility.py tests/server/test_attachment_validation_http.py -q
17 failed, 117 passed
```

Focused regression after implementation:

```text
UV_CACHE_DIR=/tmp/phytomni-bot-dataset-attachments UV_NO_SYNC=1 uv run pytest tests/server/test_api_agent_runs.py tests/server/test_api_agent_context_runs.py tests/server/test_api_app_compatibility.py tests/server/test_attachment_validation_http.py tests/unit/test_background_submission.py tests/unit/test_lifecycle_contract.py -q
172 passed in 15.35s
```

Required writable-cache scoped gate:

```text
UV_CACHE_DIR=/tmp/phytomni-bot-dataset-attachments UV_NO_SYNC=1 make scoped
```

Terminal output:

```text
# Static-analysis exemption review

- Clean: `false`
- Matched: `0`
- Unregistered: `4`
- Stale: `0`
- Duplicate registry entries: `0`
- Expired: `0`

## Unregistered findings

- `pylint:R0801` `src/mcp_server_phytomni/runtime/conversation_context/service_types.py` `sha256:0018f77095878b3c8ded46f2cfd5c9bfe9b31252d51bdc48a36fd471ca18235c`
- `pylint:R0801` `src/mcp_server_phytomni/runtime/resumable_uploads.py` `sha256:0d44371ccc8b5d55ec65a369e9c59c0959c36c85ee6502229cb5abd8c1cd8585`
- `pylint:R0801` `tests/server/test_agent_capabilities.py` `sha256:715e20b9e4ab9e0e7a4f2abf18b26fae779bde53f66b4f1047d0c2fcf493c9a6`
- `pylint:R0801` `tests/server/test_api_agent_runs.py` `sha256:ecac439b9eb4a4b55fc13703818eef2d078558d46a6886b9f97debfd354c3a18`

## Wildcard findings

None.

## Duplicate findings

None.
make: *** [Makefile:37: scoped] Error 1
```

Focused duplicate inspection after removing the Task 5 context/direct duplicate:

```text
UV_CACHE_DIR=/tmp/phytomni-bot-dataset-attachments UV_NO_SYNC=1 uv run pylint --py-version=3.12 --persistent=no tests/server/test_api_agent_runs.py tests/server/test_agent_capabilities.py src/mcp_server_phytomni/runtime/conversation_context/service_types.py src/mcp_server_phytomni/runtime/resumable_uploads.py
************* Module mcp_server_phytomni.runtime.resumable_uploads
src/mcp_server_phytomni/runtime/resumable_uploads.py:1:0: R0801: Similar lines in 2 files
==mcp_server_phytomni.runtime.conversation_context.service_types:[17:22]
==test_agent_capabilities:[145:150]
        "ChatAgent",
        "KnowledgeAgent",
        "DataAgent",
        "ReviewAgent",
        "BriefGeneAgent", (duplicate-code)
```

## Self-Review

- Owner separation: checked that explicit `owner_subject` requires
  `files:delegate` and that absent owner assertions keep the request principal
  behavior.
- Evidence containment: checked `attachment_evidence` is private plumbing only;
  responses and persisted background results are redacted via the Task 4 seam.
- Pre-202 validation: checked validation runs after projection and before native
  202 acceptance; unsupported native dataset attachments return 422 before
  reservation/invocation.
- Context replay: checked new-turn context callbacks prepare once and replay
  does not call dataset-description completion again.
- Request snapshot safety: checked native run `request_json` is metadata-only
  and excludes attachment IDs, delegated owners, dataset descriptions, dataset
  filenames, and prompt text.
- Scope boundaries: no plan, handoff, `.codex`, MCP schema, shared options, or
  shared query-resolution files were modified.

## Commits

- Pending at report write time; the final response records the created commit
  ID after this report is staged and committed.

## Concerns

- `make scoped` does not pass because the final static-analysis exemption
  review reports unregistered `pylint:R0801` duplicate-code fingerprints. After
  removing the Task 5-created duplicate between the new context/direct tests, a
  raw focused Pylint run reports only an out-of-scope duplicate between
  `src/mcp_server_phytomni/runtime/conversation_context/service_types.py` and
  `tests/server/test_agent_capabilities.py`. Those files are outside this task's
  allowed modification scope, so I did not change them or add exemptions.
