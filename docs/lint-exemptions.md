# Lint Exemptions

This document is the canonical catalog of every place where a lint tool
(ruff or pylint) is silenced against its default behaviour. The catalog
exists so that every exemption is auditable: a future maintainer can
look up *why* a violation is not reported, *what* would be involved in
removing the exemption, and *what* would have to change in the
codebase or in upstream constraints before the exemption could be
retired.

If you are about to add a new exemption, append a section to this
file using the template at the bottom. If you are about to *remove*
one, run the refactor cost estimate first against current code; the
estimates in this file are pinned to the commit that introduced the
exemption and may have changed.

## Why this catalog exists

Lint exemptions are technical debt with positive expected value: a
real codebase has patterns where the lint rule's default trade-off is
wrong, and silencing the rule locally is cheaper and safer than
restructuring the code. The risk is that exemptions accumulate
silently — a project-wide `disable` blanket-permits future violations
to slip in, and over time the codebase drifts away from the policy
the lint rule was meant to enforce.

This catalog plus the enforcement mechanisms below give us three
properties:

1. **Every exemption is named.** No silent project-wide disables.
1. **Adding a new exemption is visible.** It requires editing an
   allowlist or baseline, which shows up in code review.
1. **Each exemption documents its own sunset condition.** When the
   upstream constraint changes (e.g. SDK adds snake_case aliases,
   or pylint adds per-file thresholds), the catalog tells you how to
   retire the exemption.

## Classification

Exemptions fall into three tiers based on how the silencing is
expressed.

**L0 — refactor.** The rule fires on a pattern where refactoring is
net-positive and cheap (under one commit). No exemption; the code
moves to clear the rule. Default tier; zero drift surface.

**L1 — narrowly-scoped disable.** The rule fires on a pattern where
refactor is net-negative and the violation is local to one file or
function. Silenced with `# pylint: disable=...` at the narrowest
scope (function or class) plus an entry in
`tests/unit/test_style_naming.py:ALLOWED_LOCAL_PYLINT_DISABLES`. For
ruff, the equivalent is `[tool.ruff.lint.per-file-ignores]` scoped
by glob. Drift control: a new disable requires editing the
allowlist, which shows up in the diff and forces review.

**L2 — project-wide config + baseline ratchet.** The rule fires on a
pattern where refactor is net-negative *and* the rule cannot be
silenced locally (cross-file rules such as R0801 duplicate-code).
Silenced with a combination of project-wide pylint configuration
(when the rule has no per-file knob) plus
`scripts/check_pylint_baseline.py`, which pins the current
violation count. Drift control: a new violation pushes the count
above the baseline, the ratchet script fails, and the change author
must either refactor or explicitly bump the baseline.

The codebase enforces the L1 mechanism through two existing structural
tests:

- `test_global_pylint_disables_are_not_reintroduced` requires that
  `[tool.pylint."messages control"].disable` stays empty.
- `test_local_pylint_disables_are_langgraph_boundary_only` scans
  every tracked `.py` file for `# pylint: disable=...` comments and
  requires each to be registered in `ALLOWED_LOCAL_PYLINT_DISABLES`.

The L2 ratchet is enforced by `scripts/check_pylint_baseline.py`,
which runs after pylint in `scripts/validate_local.sh`.

## Drift prevention summary

Four representative drift vectors and what blocks each:

**A new function ends up with 14 arguments.** A naive
project-wide `max-args = 15` would silently pass it. This catalog
does not loosen `max-args` project-wide. A 14-argument function
would need `# pylint: disable=too-many-arguments` plus an allowlist
entry plus a catalog section, none of which can be added silently.

**Someone adds a 19th test fake with one public method.** A
project-wide `min-public-methods = 1` would silently pass it.
Counted instead by the L2 ratchet. The current baseline is 18; the
19th fake pushes the count to 19, the ratchet fails, refactor or
explicit baseline bump is forced.

**A 96th R0801 duplicate-code violation appears.** Default pylint
would emit a warning but the gate uses similar-lines tolerance.
Counted instead by the L2 ratchet. The current baseline is 95; the
96th duplicate fails.

**A new stub mirroring a different external SDK is added under
`typings/`.** Ruff per-file-ignores covers
`typings/**/*.pyi` wholesale, so the new file would naturally be
silenced. The catalog therefore requires a new sub-section under
the typings entry naming the new SDK and its naming convention.
Pure-ignore drift is acceptable here because the typings exemption
is a structural rule about the directory, not about a specific
file.

## Review cycle

This catalog should be reviewed end-to-end at the cadence of every
major dependency upgrade (pydantic, mypy, pylint, ruff) and at the
start of every quarter. For each entry, check:

- Has the upstream constraint named in *Sunset condition* changed?
- Has the refactor cost changed (e.g. an abstraction was added that
  makes the previously-expensive refactor cheap)?
- Is the violation count still equal to the baseline?

When an entry can be retired, remove the exemption, ratchet the
baseline down, and delete the catalog section in the same commit.

______________________________________________________________________

## Ruff exemptions

### `typings/**/*.pyi` — PEP 8 naming rules

**Rule(s)**: N802 (function names should be lowercase), N803 (argument
names should be lowercase), N815 (mixedCase variable in class scope).
26 violations on `typings/obs/__init__.pyi` at the time of the
exemption.

**Mechanism**: `pyproject.toml` `[tool.ruff.lint.per-file-ignores]`
maps `"typings/**/*.pyi"` to `["N802", "N803", "N815"]`. Structural
ruff checks (import sort, dead code, the rest of the N family) still
run on these files.

**Original error sample**:

```text
N815 Variable `requestId` in class scope should not be mixedCase
N802 Function name `getObject` should be lowercase
N803 Argument name `bucketName` should be lowercase
(typings/obs/__init__.pyi, 26 occurrences across N802/N803/N815)
```

**Why refactor is net-negative**: `typings/obs/__init__.pyi` mirrors
the upstream `esdk-obs-python` SDK's public API. Renaming the stub
identifiers would break the type-check correspondence the stub exists
to provide — mypy and pyright would then no longer associate the
camelCase calls in `src/mcp_server_phytomni/storage/uploads.py` with
the stub at all. The stub *must* use the same identifier shape as
the SDK.

**Refactor path (if you disagree)**: there is none that preserves the
stub's purpose. The only way to drop the exemption would be for the
upstream SDK itself to gain snake_case aliases, which is outside
our control.

**Refactor cost**: not applicable.

**Sunset condition**: `esdk-obs-python` ships snake_case aliases for
its public surface, or the project drops dependency on the OBS SDK
entirely.

______________________________________________________________________

## PyMarkdown exemptions

### MD013 line-length — disabled project-wide

**Rule(s)**: MD013 line-length (`plugins.md013`). 200+ occurrences
across `README.md`, `docs/**.md`, and the long-form integration
decision records when enabled with the default 80-char threshold.

**Mechanism**: `pyproject.toml` `[tool.pymarkdown]` sets
`plugins.md013.enabled = false` so pymarkdown skips line-length
enforcement entirely.

**Original error sample**:

```text
MD013: Line length [Expected: 80, Actual: 159] (line-length)
MD013: Line length [Expected: 80, Actual: 168] (line-length)
```

**Why refactor is net-negative**: the repository's other markdown
gate, mdformat, is invoked with `--wrap keep` and never reflows prose
or tables. Re-enabling MD013 would put pymarkdown in conflict with
mdformat: pymarkdown demanding ≤ 80 chars, mdformat declining to
break the line. The two tools would never agree on the same markdown
file. mdformat is the sole authority on markdown line shape, the same
way `[tool.black]` / `[tool.ruff]` own Python line shape.

**Refactor path (if you disagree)**:

1. Switch mdformat to `--wrap=80` (reflows prose).
1. Re-enable `plugins.md013.enabled = true`.
1. Re-run mdformat to reflow every existing long line.
1. Verify tables stay readable after `--wrap=80` reflow (mdformat
   wraps table cells too, which sometimes breaks alignment).
1. Add an mdformat test that pins the wrap setting so a future
   `--wrap keep` revert does not silently reopen the conflict.

**Refactor cost**: every existing long line in the repo gets
rewrapped, including code blocks and tables. The diff would touch
every markdown file. Table-heavy docs may need manual fix-ups for
broken alignment. ~3-5 commits and a careful review pass.

**Sunset condition**: the project switches mdformat to a reflowing
wrap mode (e.g. `--wrap=80`), or pymarkdown adds a "trust
mdformat's wrap policy" flag that disables MD013 only when an
external formatter owns the line shape.

______________________________________________________________________

## Pylint exemptions

### `typings/` directory — wholesale ignore

**Rule(s)**: W0613 (unused-argument), C0103 (invalid-name), R0903
(too-few-public-methods), C0116 (missing-function-docstring), R0913
(too-many-arguments), R0917 (too-many-positional-arguments).
75 violations on `typings/obs/__init__.pyi` at the time of the
exemption.

**Mechanism**: `pyproject.toml` `[tool.pylint.main].ignore` includes
`"typings"`. Pylint walks past the directory entirely.

**Why refactor is net-negative**: same as the ruff entry above. Stubs
mirror an external SDK; the parameter names and class shapes are
fixed by upstream. mypy and pyright are the canonical stub checkers,
wired via `[tool.pyright].stubPath` and `[tool.mypy].mypy_path`.

**Why a wholesale ignore (not per-rule)**: pylint has no analogue to
ruff's `per-file-ignores`. The options are a directory-level `ignore`
(used here), file-level `# pylint: disable=...` comments at the top
of every stub (would duplicate the policy across every future
`.pyi`), or inline disables on every offending line (the loudest of
the three). The directory-level ignore is the only choice that
scales to additional SDK stubs without policy duplication.

**Refactor path (if you disagree)**: same as ruff — would require the
upstream SDK to add snake_case aliases.

**Refactor cost**: not applicable.

**Sunset condition**: same as ruff.

______________________________________________________________________

### `agents/chat/service.py:run_phyto_chat_cached` — cache-key primitive

**Rule(s)**: R0913 too-many-arguments (15/8), R0914 too-many-locals
(19/15).

**Mechanism**: function-level
`# pylint: disable=too-many-arguments,too-many-locals` on the
`run_phyto_chat_cached` definition. Allowlist entry under
`src/mcp_server_phytomni/agents/chat/service.py`.

**Original error**:

```text
agents/chat/service.py:377:0: R0913: Too many arguments (15/8)
agents/chat/service.py:377:0: R0914: Too many local variables (19/15)
```

**Why refactor is net-negative**: `run_phyto_chat_cached` is the
`@func_cache` chokepoint for chat LLM completions. `func_cache` keys
on each named parameter listed in `key_params`. The 15 parameters are
all semantic inputs to the LLM call (model id, messages, temperature,
top_p, max_tokens, presence/frequency penalties, stop tokens, tools,
response format, etc.). Packaging them into an options object would
make the cache key `hash(options)`, and options would necessarily
include infrastructure fields (api_key, base_url, timeout) that
must *not* affect cache hits — rotating an API key should not invalidate
the cache. The flat parameter list is the cache contract.

**Refactor path (if you disagree)**:

1. Split `ChatCacheKey` (semantic) and `ChatCacheInfra` (infra) types.
1. Extend `func_cache.key_params` to support nested dataclass field
   selectivity, so `key_params=["chat_key.*"]` includes only the
   semantic subtree.
1. Rewrite every cache primitive (knowledge retrieval, nl2sql, brief
   gene) onto the new shape.
1. Backfill a cache-key bit-stability test across e2e to confirm no
   key changes.

**Refactor cost**: 5-8 commits. Pollutes the `func_cache` abstraction
across all callers. Requires a full chat / knowledge / nl2sql /
brief_gene e2e regression run.

**Sunset condition**: `func_cache` gains nested-selective key params,
or the chat primitive's semantic input set shrinks to ≤ 8 fields.

______________________________________________________________________

### `agents/knowledge/retrieval.py` — cache-key primitives (3 functions)

**Rule(s)**: R0913 too-many-arguments at lines 411 (10/8), 545 (12/8),
797 (8/8).

**Mechanism**: function-level
`# pylint: disable=too-many-arguments` on each of
`_retrieve_scope_docs`, `_rerank_batch`, `_multi_retrieve`. Allowlist
entry under `src/mcp_server_phytomni/agents/knowledge/retrieval.py`.

**Why refactor is net-negative**: same as chat — these are `func_cache`
chokepoints. Their parameter lists are the cache contract.

**Refactor path / cost / sunset**: same as the chat entry.

______________________________________________________________________

### `agents/data/nl2sql.py:_execute_nl2sql_cached` — cache-key primitive

**Rule(s)**: R0913 too-many-arguments (7/8), R0917
too-many-positional-arguments (6/5).

**Mechanism**: function-level
`# pylint: disable=too-many-arguments,too-many-positional-arguments`.
Allowlist entry under `src/mcp_server_phytomni/agents/data/nl2sql.py`.

**Why refactor is net-negative**: same cache-key reason. The 7
params split into 5 semantic inputs (`message_content`, `subject`,
`workspace`, `database`, `insight`) plus 2 infra parameters
(`dialog_id` and `token`); the infra parameters are excluded from
`key_params` but still ride the call signature. The only way to
drop them would be to push them into a thread-local, which
introduces hidden state into a primitive whose value comes from
being explicit.

**Refactor path / cost / sunset**: same as chat.

______________________________________________________________________

### `storage/uploads.py:upload_user_file` — conceptually-atomic OBS upload

**Rule(s)**: R0913 too-many-arguments, R0914 too-many-locals.

**Mechanism**: function-level
`# pylint: disable=too-many-arguments,too-many-locals` above the
`upload_user_file` definition. Allowlist entry under
`src/mcp_server_phytomni/storage/uploads.py`.

**Original error sample**:

```text
storage/uploads.py: R0913: Too many arguments (upload_user_file)
storage/uploads.py: R0914: Too many local variables (upload_user_file)
```

**Why refactor is net-negative**: `upload_user_file` is a single
conceptually-atomic OBS upload — validate the body, resolve the
per-user OBS target, then write — and its parameters (`file_bytes`,
`original_filename`, `user_id`, `request_id`, `max_bytes`, `prefix`,
`bucket_name`, `obs_server`, `obsfs_mount_root`) are all distinct
inputs to that one operation. Packaging them into a dataclass would
add caller boilerplate at every call site without splitting the
responsibility the function owns.

**Refactor path (if you disagree)**:

1. Introduce an `UploadRequest` dataclass grouping the validation and
   target-resolution fields.
1. Update every caller to construct and pass the dataclass.

**Refactor cost**: 1 commit, mechanical, but adds construction
boilerplate at every call site for no separation-of-concerns gain.

**Sunset condition**: the function's responsibility genuinely splits
(e.g. validation and storage become independently reusable), at which
point the parameter set naturally divides across the two functions.

______________________________________________________________________

### `api/app.py` — FastAPI factory and route handlers

**Rule(s)**: C0302 too-many-lines (1641/1000), R0915
too-many-statements (76/50 at line 764), R0913 (8 at 443, 12 at 1093),
R0914 (20 at 275, 19 at 764, 16 at 1093).

**Mechanism**: file-level
`# pylint: disable=too-many-lines` at the top of `api/app.py`, plus
function-level
`# pylint: disable=too-many-arguments,too-many-locals,too-many-statements`
on `create_app`, `_resolve_remote_run`, and the route handlers at
lines 275, 443, 764, 1093. Allowlist entry under
`src/mcp_server_phytomni/api/app.py`.

**Why refactor is net-negative**: `create_app` is the FastAPI
registration centre that wires every route, exception handler, and
middleware into one app instance in one closure. Splitting the body
across multiple `register_routes_*` modules requires each module to
re-import the app, the auth dependency, the rate-limit dependency,
the task DB dependency, and the request-context middleware. The
sliced-up version is harder to read because the route → handler →
middleware → exception-handler graph is no longer co-located.

The individual route handlers (e.g. `_resolve_remote_run`) accumulate
locals as they walk request validation → DB lookup → status
reconciliation → response assembly. Extracting each step into a
helper requires passing 5-7 context variables through the helper
chain because FastAPI's dependency injection does not cross
ordinary helper boundaries — the result has the same complexity
distributed across more function boundaries.

**Refactor path (if you disagree)**:

1. Split `api/app.py` into `api/app_factory.py`, `api/routes_chat.py`,
   `api/routes_runs.py`, `api/routes_agents.py`, `api/routes_keys.py`.
1. Move each `@app.post(...)` decorator and its body into the
   corresponding module.
1. Introduce a `RegisterRouter` protocol so each module can be picked
   up by the factory.
1. Full HTTP API e2e regression to confirm no behaviour change.

**Refactor cost**: 2-3 commits, full e2e suite required, result is
less locally-readable than the current single-file factory.

**Sunset condition**: the file grows beyond ~1500 lines (at which
point the readability argument flips), or FastAPI adds a first-class
"routes module" registration pattern that eliminates the
re-import-everywhere problem. The ~1500-line trigger has now been
reached (the module is 1641 lines), so the split outlined in the
refactor path above should be re-evaluated.

______________________________________________________________________

### W0135 contextmanager false positive in test fixtures

**Rule(s)**: W0135 contextmanager-generator-missing-cleanup. 6
occurrences across 2 files.

**Mechanism**: file-level
`# pylint: disable=contextmanager-generator-missing-cleanup` placed
near the top of each file (after the imports, before any function
definition). Allowlist entries under `tests/conftest.py` and
`tests/agents/test_cache_candidates.py`. Function-level scoping was
rejected because the trailing-comment form exceeded the 79-char line
limit and the `disable-next=` form is not detected by the
`test_local_pylint_disables_are_langgraph_boundary_only` marker
scanner (which substring-matches `pylint: disable=`). File-level
scope is acceptable here because both files host nothing but test
fixtures that use the same `@asynccontextmanager` idiom.

**Original error sample**:

```text
tests/conftest.py:332:8: W0135: The context used in function
'_build_async_factory' will not be exited.
(contextmanager-generator-missing-cleanup)
```

**Why refactor is net-negative**: the canonical
`@asynccontextmanager` + `async with X() as y: yield y` pattern is a
documented false positive — pylint's static analysis cannot see that
the decorator already converts a thrown `GeneratorExit` into the
proper `__aexit__` call. The warning never matches a real cleanup
bug in production code. The codebase confirms this on the production
side: `common.httpx_client.get_async_client` uses an *inline* class
reference and pylint passes; the test conftest uses a closure
reference (because the fake client class is constructed per test
behaviour list) and pylint trips.

**Refactor path (if you disagree)**:

1. Rewrite each `@asynccontextmanager` fixture as an explicit
   `try/finally` block.
1. Replace the `yield` with a manual `__aenter__` / `__aexit__`
   driver.

**Refactor cost**: every fixture factory grows from ~8 lines to
~25-35 lines. Test readability drops sharply because the per-test
behaviour list is now obscured by the explicit-CM scaffolding.

**Sunset condition**: pylint learns to recognise the
`@asynccontextmanager` + closure-class pattern, or the test fixtures
are restructured around inline class definitions (which would
require re-engineering the per-test behaviour script API).

______________________________________________________________________

### W0212 protected-access in test helpers

**Rule(s)**: W0212 protected-access. Per-file count varies; this
section currently covers `tests/agents/test_design_helpers.py` and
will likely grow as further Phase C lifts add tests that target
internal helpers directly.

**Mechanism**: file-level
`# pylint: disable=protected-access` placed near the top of each
test file that reaches into a non-public helper. Allowlist entry
under the file path in `ALLOWED_LOCAL_PYLINT_DISABLES`.

**Original error sample**:

```text
W0212: Access to a protected member _analysis_prompt_parts of a
client class (protected-access)
```

**Why refactor is net-negative**: pytest's "test the smallest unit
the bug can hide in" convention often requires reaching into
underscore-prefixed helpers — they ARE the unit under test. The
alternatives (promote the helper to public, or test it only through
its public caller) either widen the API surface or weaken the
coverage. Both are worse than annotating tests as a legitimate
private-method consumer.

**Refactor path (if you disagree)**: promote the helper to a public
name (drop the leading underscore) and move the convention into the
module docstring. Useful only when the helper IS the public contract
in disguise; for genuine internal helpers, the refactor degrades the
API.

**Refactor cost**: per helper, one rename in source + every caller

- a fresh round of code review on whether the helper deserves
  public-API status. Adds up across the codebase.

**Sunset condition**: pylint adds a configuration knob like
`acceptable-protected-access-paths = ["tests/**/test_*.py"]` so the
exemption is project-wide-conditional instead of per-file.

______________________________________________________________________

### Test fakes and Protocol stubs — too-few-public-methods (21 occurrences)

**Rule(s)**: R0903 too-few-public-methods (1/2). 21 occurrences (the
authoritative count is enforced by `check_pylint_baseline.py`; a
representative sample below):

- `tests/conftest.py:550`
- `tests/agents/test_chat_agent.py:427`
- `tests/agents/test_deep_genome_submit.py:32`
- `tests/agents/test_evolution_agent.py:250 + 305`
- `tests/server/test_handler_support.py:35`
- `tests/unit/test_api_file_upload.py:24`
- `tests/unit/test_deep_genome_dispatch.py:226`
- `tests/unit/test_storage_error_sanitization.py:31`
- `src/mcp_server_phytomni/agents/shared/fan_out.py:22` (`FanOutWorker` Protocol)
- `src/mcp_server_phytomni/agents/shared/fan_out.py:43` (`TaskBuilder` Protocol)
- `src/mcp_server_phytomni/runtime/terminal_answer.py` (`TerminalAnswerContext` DTO + `AnswerSynthesizer` Protocol)
- `src/mcp_server_phytomni/runtime/terminal_artifacts.py` (`ArtifactLister` Protocol)
- `tests/agents/test_brief_gene_preamble_workflow.py:55` (`_StubKnowledgeApp` preamble fan-in test stub)

**Mechanism**: L2 baseline ratchet via
`scripts/check_pylint_baseline.py` (`RULE_BASELINES["R0903"] = 21`).
The main pylint invocation in `scripts/validate_local.sh` and
`scripts/scoped_gate.sh` is run with `--disable=R0801,R0903` so the
gate-level pylint exits 0 on this rule; the baseline script runs its
own pylint without the disable and counts violations against the
pinned baseline. No function-level disable, because the rule fires
once per fake class and the class is the smallest unit where the
disable could attach — using L1 disable would mean editing
the allowlist for every fake.

**Why refactor is net-negative**: each fake class exists to mock
exactly one method of an external SDK (e.g.
`class FakeOBS: def putContent(self, ...)`). The class has one
public method *by definition of its purpose*; the count is
structurally `1`. The alternative — replacing the fake class with
an `AsyncMock(side_effect=...)`
— makes the test setup more abstract and less self-documenting
(`mock.method.assert_called_with(...)` errors are harder to read than
`fake.captured["key"]`).

**Refactor path (if you disagree)**: for each fake, convert to
`AsyncMock` or `MagicMock` with explicit `side_effect` callables.
Update the corresponding test assertions to use `mock.call_args`
instead of fake-captured-state assertions.

**Refactor cost**: ~10 commits, one per file. Mechanical but each
diff is reviewable and small. The catch is that every test that
asserts against the fake's captured state needs an assertion-style
change.

**Sunset condition**: the codebase migrates from hand-rolled fakes
to mock-library patterns wholesale (orthogonal-scope refactor that
should be planned independently).

______________________________________________________________________

### R0801 duplicate-code (25 occurrences)

**Rule(s)**: R0801 similar-lines-in-files. 25 violations across the
codebase, in nine clusters:

1. **Analyst module fan-out wrappers** (~6 occurrences). The
   `analyst/__init__.py`, `analyst/agent.py`, and `analyst/defaults.py`
   share `__all__` listings; `analyst/core.py`, `analyst/submission.py`,
   `analyst/planning.py` share 5-7 line kwargs blocks because they
   fan out to one backend with parallel signatures.
1. **HTTP API test boilerplate** (~10 occurrences). Several
   `tests/server/test_api_*.py` files contain similar 5-10 line API
   call setup blocks.
1. **MCP result formatting test fixtures** (~4 occurrences). Helper
   payload assembly is repeated across
   `tests/server/test_result_formatting*.py`.
1. **LangGraph wrapper `arun` body, environment vs evolution**
   (1 occurrence). `agents/environment/agent.py` and
   `agents/evolution/agent.py` share the same
   `initial_state → ainvoke_graph → return final_state["result"]`
   sequence introduced when both agents grew graph wrappers in
   parallel. The two callers want their own per-agent state typing
   and result post-processing, so a generic wrapper helper would
   force a shape-erased state dict that loses each agent's typed
   keys.
1. **Chat-agent kwargs spread, stream vs non-stream** (1 occurrence,
   added 2026-05-28). `mcp/app.py::_stream_chat_agent` and
   `mcp/handlers.py::handle_chat_agent` build the same 5-line
   `user_query / obs_file_list / server_dir / chat_kwargs / obs_kwargs` block. The duplicate is intentional per the streaming
   plan: the stream primitive is a sister of the cached path
   (`run_phyto_chat_cached`), not a wrapper, so the two call sites
   must hand the provider identical config / sensitive / OBS wiring.
1. **Frozen Pydantic manifest classes** (1 occurrence, added
   2026-05-28 with `f59b7a2`). `graphs/manifest.py`'s
   `GraphNodeManifest` / `GraphEdgeManifest` / `GraphManifest` and
   `graphs/spec.py`'s `SubgraphSpec` share the same
   `model_config = ConfigDict(frozen=True)` + `Field(min_length=1)`
   shape with several existing config classes (e.g.
   `agents/*/defaults.py`). The shape is the public contract for
   "immutable manifest with non-empty validated identifiers" — every
   class instantiates it because that is the right idiom, not
   because the classes should share a base. The role split between
   `SubgraphRegistry` and `runtime.langgraph_runner.GraphRegistry`
   is independently documented in `docs/agent-graphs.md`.
1. **Bootstrap env install dict, viz vs root conftest** (1
   occurrence, added 2026-05-28 with `a81ce97`).
   `scripts/_visualize_bootstrap.py` mirrors the env-var key list
   in the root `conftest.py` because both bootstrap a "fake
   deployment env" before any `mcp_server_phytomni` import runs;
   only the placeholder VALUE prefixes differ (`viz-*` vs
   `pytest-*`). Sharing a key list module would force
   `scripts/_visualize_bootstrap.py` to import from `tests/` or
   `src/` (layer violation in both directions), so the duplication
   is the lesser evil.
1. **Cited-agent generate→follow_up routing topology** (1
   occurrence, added 2026-05-29). `agents/brief_gene/core.py`
   and `agents/knowledge/agent.py` share the same
   `add_conditional_edges("generate_node", route_after_generate, {"follow_up_node": "follow_up_node", "__end__": END}) + add_edge("follow_up_node", END) + workflow.compile(...)`
   block followed by the next-method docstring. The shape is the
   intentional symmetry between cited-style agents (both expose
   an `is_follow_up` state-field toggle so parent graphs may skip
   the trailing follow-up LLM hop); a shared helper would erase
   each agent's domain-specific node and state typing.
1. **Analyst submit kwargs, shared.analysis vs deep_genome dispatch**
   (added 2026-05-31, **CLOSED 2026-06-07** by the Step 6.5 cluster
   #9 sunset commit). `agents/shared/analysis.py`'s
   `submit_analyst_analysis` and `agents/deep_genome/dispatch.py`'s
   `_run_analyst_node` historically built the same
   `analyst_agent.arun(query=None, goal_description=..., preset_data_list=..., preset_plan=..., output_dir=..., compute_resource=...)`
   call. The dispatch-side inline call was retired: deep_genome's
   `_submit_analysis_task` routes through `submit_analyst_via_subgraph`
   with `is_polling=True` (the `USE_ANALYST_SUBGRAPH` flag-off
   fallback to `submit_analyst_analysis` was later removed with the
   flag). The bare arun-kwargs block exists only inside
   `submit_analyst_analysis` now, so the cross-file mirror is gone.
   The catalog entry stays as a historical record so future readers
   can trace the closure without diff-archaeology.

**Mechanism**: L2 baseline ratchet via
`scripts/check_pylint_baseline.py` (currently
`RULE_BASELINES["R0801"] = 95`). The catalog header count above
(25) reflects an older snapshot; subsequent Phase-6 / F-series
steps ratcheted the baseline through 52 (F1 close), 58 (F2.C2 plus
AF-6 coverage lift), 63 (F3.C3.3 Send-triad worker mirroring
analyst / data retrieve-fan-out templates), 66 / 79 / 93 / 94
(brief_gene preamble fusion + Align-A citation rules accumulation,
documented in their own commit bodies), 97 (Step 6.4 env/evo per-
consumer wiring; see entry below), 102 (Step 6.5 producer-
wrapper request-dict mirrors net of the cluster #9 sunset, see
entries below), and 110 (HTTP resolver cascade for
deep_genome / design / network — each new `resolve_query.py`
module mirrors BGA's `_first_message_content` + `_normalize_ candidates` + chat-kwargs assembly shape; intentional parallel
structure per the AGENTS.md "Per-request context + HTTP-only
pre-shaping" reference template). Each ratchet was disclosed in
its own commit body;
the original 25-cluster catalog remains accurate for the legacy
clusters but is no longer the authoritative count. The main pylint invocation in
`scripts/validate_local.sh` and `scripts/scoped_gate.sh` is run with
`--disable=R0801,R0903` so the gate-level pylint exits 0 on this
rule; the baseline script runs its own pylint without the disable
and counts the violations against the pinned baseline. A new R0801
violation pushes the count past the baseline, the baseline script
exits 1, and the gate fails until the author either resolves the
duplicate or explicitly bumps the baseline in the same diff. The three
most recent ratchets after 110 were 112 (resolver shared-helper
extraction into `agents/shared/bga_delegation.py`), 108 (removing the
five `USE_*_SUBGRAPH` flags collapsed the chat-fallback and dispatch
request-dict flag-off mirror clusters below `min-similar-lines=4`), and
95 (deleting the unwired legacy monolithic analyst / review nodes
removed every prep/post-vs-monolithic-half mirror cluster, since the
split nodes no longer have a single-node twin to duplicate).

**Why refactor is net-negative for cluster 1**: the analyst fan-out
wrappers' parallel signatures are by design — they map onto one
backend with one canonical signature. Collapsing them into a generic
helper would obscure the per-wrapper public contract.

1. **Env/evo chat branch mirror** (added 2026-06-07, **CLOSED
   2026-06-11** by the `USE_CHAT_SUBGRAPH` flag removal).
   `agents/environment/agent.py` and `agents/evolution/agent.py`
   previously shared a chat-call branch (build_chat_input →
   \_cached_chat_app().ainvoke → extract_chat_response vs a
   `phyto_chat` fallback) wrapping their chat_kwargs bag. Removing
   the chat-subgraph flag deleted the `phyto_chat` fallback, so both
   call sites shrank to a 3-line block below `min-similar-lines=4`
   and the cluster no longer fires. The catalog entry stays as a
   historical record.
1. **Module-level analyst dispatch request-dict mirror**
   (added 2026-06-07, expanded by Step 6.5 producer wrappers).
   Originally `agents/environment/graph.py:[180:187]` ↔
   `agents/evolution/graph.py:[179:186]`. Step 6.5 introduced four
   new module-level dispatch sites with the same
   (`analysis_type` / `target_id` / `output_dir` / `prompt_parts` /
   `compute_resource`) request dict shape:
   `agents/evolution/agent.py:evolution_analysis_for_gene` (since
   retired — evolution now routes through the mounted `evolution_node`
   subgraph rather than a module-level producer wrapper),
   `agents/design/agent.py:_submit_design_analysis` (consumed by
   `protein_structure_for_gene` and `promoter_design_for_gene`),
   and `agents/deep_genome/dispatch.py:_submit_analysis_task` (the
   cluster-#9 sunset commit). The sites build the same dict shape
   because `submit_analyst_via_subgraph` is the canonical chokepoint
   (the `submit_analyst_analysis` flag-off branches were removed with
   the `USE_ANALYST_SUBGRAPH` flag) and the dict shape is the
   contract. A shared `build_analyst_request`
   helper would erase the `target_id` semantics each consumer
   assigns (region-codes concat for env, gene_id for evo / design /
   deep_genome). The Phase 5a design / network sites in
   `_dispatch_and_wait_analysis` are not yet line-aligned to the
   module-level ones only because they nest the dict inside an
   instance method. **Sunset condition**: none — the parallel-
   structure design extends to every future subgraph consumer.

**Why refactor is net-negative for cluster 4**: the chat-agent
stream and non-stream call sites land in two modules
(`mcp/app.py::_stream_chat_agent` and
`mcp/handlers.py::handle_chat_agent`). The natural extraction
target — a shared kwargs builder in `mcp/handler_support.py` —
would cross-cut the in-flight chat-agent refactor a parallel agent
is staging (new `agents/chat/agent.py`, untracked at commit time);
landing the extraction now would either collide with that work or
ship a single-use helper that is itself an antipattern. A
follow-up commit can lower the baseline back to 20 once the
parallel refactor consolidates the chat call sites.

**Why refactor is net-negative for cluster 6**: the frozen
Pydantic manifest shape is a contract surface, not a structural
duplicate. Extracting a `FrozenManifestBase(ConfigDict(frozen=True), name = Field(min_length=1))` and inheriting from it would: (a) push
every manifest class onto the same base for the sake of two lines,
(b) couple `graphs/` to a shared base another module is free to
mutate, and (c) hide the per-class `Field` validators that DO
differ. The classes are siblings that happen to follow the same
pattern, not children of a common abstraction; pylint cannot tell
them apart but a human reader can.

**Why refactor is net-negative for cluster 7**: the bootstrap env
key list is shared by design — pytest tests and the visualization
script both need the same `mcp_server_phytomni` modules to import
cleanly, which requires the same env var keys populated. The
shared module that would carry the key list cannot live in `src/`
(production package should not ship dev-only env data), cannot
live in `tests/` (the visualization script is not a test), and
cannot live in `scripts/` (the root `conftest.py` should not
import from `scripts/`). Each candidate path is a layer
violation; duplicating the 32-entry key list is the lesser evil
until a dedicated `dev_tooling/` or similar top-level dir exists.

**Why refactor is mixed for clusters 2 and 3**: the test boilerplate
could plausibly be extracted into pytest fixtures. The reason it has
not been is that each extraction creates a new coupling point that
makes the test suite harder to read in isolation. The ratchet keeps
this option open: the count can be lowered as fixtures are extracted
in opportunistic refactors, but new duplicate test boilerplate is
blocked from sneaking in.

**Refactor path for clusters 2 and 3 (if you disagree)**:

1. Identify the 5-10 most-repeated test boilerplate patterns.
1. Extract each into a fixture in `tests/conftest.py` or a
   sub-directory conftest.
1. Update the affected tests to use the fixture.
1. Ratchet the baseline down to the new measured count.

**Refactor cost**: ~5-10 commits, one per fixture cluster.
Diminishing return because each subsequent extraction is smaller.

**Sunset condition**: the residual count after cluster-2-and-3
extraction falls below 5 (at which point the project-wide threshold
override could be retired and the few remaining cases handled via
allowlist).

______________________________________________________________________

## Template for a new exemption

When adding a new exemption, copy this section under the appropriate
`Ruff exemptions` or `Pylint exemptions` heading and fill in every
field.

```markdown
### `<file:line or path pattern>` — <one-line description>

**Rule(s)**: <code> <name> (<actual>/<threshold>). <count> occurrences.

**Mechanism**: <one-line summary of where the silencing lives>.
Allowlist entry under `<path in ALLOWED_LOCAL_PYLINT_DISABLES>`
or baseline entry under
`scripts/check_pylint_baseline.py:PYLINT_VIOLATION_BASELINE`.

**Original error sample**:

\`\`\`text
<copy-paste a representative error line>
\`\`\`

**Why refactor is net-negative**: <2-4 sentences naming the
constraint that makes the rule's default trade-off wrong for this
case>.

**Refactor path (if you disagree)**:

1. <step>
1. <step>

**Refactor cost**: <commits, scope, regression footprint>.

**Sunset condition**: <observable upstream or codebase change that
would let this exemption retire>.
```
