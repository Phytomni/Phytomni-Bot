# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for repository naming and header conventions."""

import ast
import configparser
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PYTHON_FILE_PATTERN = re.compile(r"^(_?[a-z][a-z0-9_]*|__init__)\.py$")
COPYRIGHT_HEADER = (
    "# Copyright (c) Biotechnology Research Institute,",
    "# Chinese Academy of Agricultural Sciences. 2024-2026. "
    "All rights reserved.",
)
AUTHOR_FIRST_LINE_PATTERN = re.compile(
    r"^# Author: [A-Za-z0-9_.-]+ \([^@\s)]+@[^@\s)]+\.[^@\s)]+\)$"
)
AUTHOR_CONTINUATION_PATTERN = re.compile(
    r"^#         [A-Za-z0-9_.-]+ \([^@\s)]+@[^@\s)]+\.[^@\s)]+\)$"
)
PYLINT_DISABLE_MARKER = "".join(("pylint:", " disable="))
ALLOWED_LOCAL_PYLINT_DISABLES = {
    # FastAPI factory + route handlers: see
    # ``docs/development/lint-exemptions.md`` entries on
    # ``api/app.py``. ``too-many-lines`` is file-level; the rest
    # scope per-function via bracketed disable/enable pairs.
    "src/mcp_server_phytomni/api/app.py": {
        "too-many-lines",
        "too-many-arguments",
        "too-many-locals",
        "too-many-statements",
        "broad-exception-caught",
    },
    # Dispatch-boundary formatters: see
    # ``docs/development/lint-exemptions.md`` entry on
    # ``mcp/result_formatting.py``.
    "src/mcp_server_phytomni/mcp/result_formatting.py": {
        "too-many-lines",
        "too-many-locals",
    },
    # Opened-stream lifecycle projector: the exception boundary must catch
    # every ordinary producer failure while leaving cancellation untouched.
    "src/mcp_server_phytomni/mcp/stream_lifecycle.py": {
        "broad-exception-caught",
    },
    # @func_cache chokepoint: see
    # ``docs/development/lint-exemptions.md`` entry on
    # ``run_phyto_chat_cached``.
    "src/mcp_server_phytomni/agents/chat/service.py": {
        "too-many-arguments",
        "too-many-locals",
    },
    # @func_cache chokepoints (3 functions): see
    # ``docs/development/lint-exemptions.md`` entry on
    # ``knowledge/retrieval.py``.
    "src/mcp_server_phytomni/agents/knowledge/retrieval.py": {
        "too-many-arguments",
    },
    # @func_cache chokepoint: see
    # ``docs/development/lint-exemptions.md`` entry on
    # ``_execute_nl2sql_cached``.
    "src/mcp_server_phytomni/agents/data/nl2sql.py": {
        "too-many-arguments",
        "too-many-positional-arguments",
    },
    # Conceptually-atomic OBS upload: see
    # ``docs/development/lint-exemptions.md`` entry on
    # ``upload_user_file``.
    "src/mcp_server_phytomni/storage/uploads.py": {
        "too-many-arguments",
        "too-many-locals",
    },
    # Polling coordinator injection seam: callbacks and timing controls stay
    # explicit so optional-job tests can deterministically drive each path.
    "src/mcp_server_phytomni/agents/deep_genome/coordinator.py": {
        "too-many-arguments",
    },
    # DeepGenome's coordinator mixin keeps submission, polling, and
    # owner-scoped transition projection in one boundary; see the catalog.
    "src/mcp_server_phytomni/agents/deep_genome/dispatch.py": {
        "too-many-lines",
        "too-many-arguments",
        "too-many-positional-arguments",
        "too-many-locals",
    },
    # Lifecycle tests intentionally exercise protected coordinator seams and
    # Pydantic-style uppercase config fields on lightweight fakes.
    "tests/agents/test_deep_genome_lifecycle.py": {
        "protected-access",
        "too-many-locals",
    },
    # Pytest treats ``tests/`` as a namespace package, but the standalone
    # pylint invocation does not add the repository root to its import path.
    # The A2UI stream tests intentionally share the API stream fixtures.
    "tests/server/test_a2ui_chat_streaming.py": {
        "import-error",
    },
    "tests/server/test_a2ui_review_http.py": {
        "import-error",
    },
    # conftest.py installs deployment env vars BEFORE importing any
    # project module — several agents construct ``ServerConfig()`` at
    # import time and the per-deployment endpoints are required-via-
    # env, so importing before the install raises ``ValidationError``.
    # The deliberate import-after-setup ordering trips C0413; the
    # bracketed disable above + enable below scope the exemption to
    # the install block only.
    # ``contextmanager-generator-missing-cleanup`` is documented in
    # ``docs/development/lint-exemptions.md`` as a false-positive on the
    # ``@asynccontextmanager`` + closure-class fake-client pattern.
    "tests/conftest.py": {
        "wrong-import-position",
        "contextmanager-generator-missing-cleanup",
    },
    # Same W0135 false positive as conftest.py; see
    # ``docs/development/lint-exemptions.md``.
    "tests/agents/test_cache_candidates.py": {
        "contextmanager-generator-missing-cleanup",
    },
    # Test exercises design-agent internal helpers
    # (``_get_compute_resource``, ``_analysis_prompt_parts``); see
    # ``docs/development/lint-exemptions.md`` "W0212 protected-access in test
    # helpers" entry.
    "tests/agents/test_design_helpers.py": {
        "protected-access",
    },
    # Branch + invocation tests for analyst-subgraph dispatch:
    # exercises ``DigitalDesignAgents._dispatch_and_wait_analysis``
    # directly to assert it dispatches via the analyst subgraph.
    "tests/agents/test_design_analyst_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``GeneNetworkAgents._dispatch_and_wait_analysis``
    # — same protected-helper coverage seam as the design sibling.
    "tests/agents/test_network_analyst_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``InSilicoResearchAgents._submit_research_task``
    # — same protected-helper coverage seam as the design sibling.
    "tests/agents/test_research_analyst_subgraph.py": {
        "protected-access",
    },
    # Test for ``InSilicoResearchAgents._extract_goals`` — asserts
    # the chat call site routes through the compiled chat subgraph,
    # same protected-helper coverage seam.
    "tests/agents/test_research_chat_subgraph.py": {
        "protected-access",
    },
    # Test for ``DeepGenomeDispatchMixin._submit_analysis_task`` —
    # asserts the 3 transferred analysis types route to the evolution
    # / design producer wrappers, same protected-helper coverage seam.
    "tests/agents/test_deep_genome_dispatch_routing.py": {
        "protected-access",
    },
    # Test for ``DeepGenomeReportMixin._dispatch_chat`` — asserts
    # the report node bodies' chat call sites route through the
    # compiled chat subgraph, same protected-helper coverage seam.
    "tests/agents/test_deep_genome_chat_subgraph.py": {
        "protected-access",
    },
    # Test for ``DeepGenomeReportMixin._dispatch_knowledge_retrieve``
    # — asserts ``_experiment_protocols`` routes through the compiled
    # knowledge subgraph, same protected-helper coverage seam.
    "tests/agents/test_deep_genome_knowledge_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``AnalystAgent._knowledge_app`` lifecycle —
    # asserts the per-instance KA app is built only when the flag is
    # on; same protected-helper coverage seam as the design sibling.
    "tests/agents/test_analyst_knowledge_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``DataAgent._knowledge_app`` lifecycle —
    # asserts the per-instance KA app is built only when the flag is
    # on; same protected-helper coverage seam as the analyst sibling.
    "tests/agents/test_data_knowledge_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``DeepResearchAgent._knowledge_app``
    # lifecycle — asserts the per-instance KA app is built only when
    # the flag is on; same protected-helper coverage seam as the
    # analyst / data siblings.
    "tests/agents/test_review_retrieve_fan_out.py": {
        "protected-access",
    },
    # Flag-branch test for ``DeepResearchAgent`` draft fan-out — same
    # protected-helper coverage seam as the retrieve fan-out sibling.
    "tests/agents/test_review_draft_fan_out.py": {
        "protected-access",
    },
    # Flag-branch test for ``BriefGeneAgent._knowledge_app`` lifecycle
    # — asserts the per-instance KA app is built only when the flag is
    # on; same protected-helper coverage seam as the analyst / data /
    # review siblings.
    "tests/agents/test_brief_gene_knowledge_subgraph.py": {
        "protected-access",
    },
    # Flag-branch test for ``DeepResearchAgent`` review_results fan-out
    # — same protected-helper coverage seam as the draft fan-out sibling.
    "tests/agents/test_review_review_results_fan_out.py": {
        "protected-access",
    },
    # Flag-branch test for ``DeepResearchAgent`` revised fan-out — same
    # protected-helper coverage seam as the review_results sibling.
    "tests/agents/test_review_revised_fan_out.py": {
        "protected-access",
    },
    # Per-call failure tests for ``DeepResearchAgent._feedback_rag`` —
    # same protected-helper coverage seam as the revised fan-out sibling.
    "tests/agents/test_review_add_query_failures.py": {
        "protected-access",
    },
    # Per-call-site assertions on ``DeepResearchAgent`` chat-subgraph
    # follow-up routing — same protected-helper coverage seam as the
    # fan-out siblings; touches ``_build_agent`` / prep / route hooks.
    "tests/agents/test_review_follow_up_routing.py": {
        "protected-access",
    },
    # Test for ``AnalystGraphMixin._submit_output_dir`` — asserts the
    # fingerprint from state is forwarded to ``ensure_run_output_dir``
    # so the output dir routes to the content-addressed shared key.
    # Same protected-helper coverage seam as the design/network siblings.
    "tests/agents/test_analyst_graph_nodes.py": {
        "protected-access",
    },
    # Test for ``_schedule_run_gc`` (a module-private dependency function
    # on api/app.py) — asserts the three sync write routes declare it
    # via route-introspection; same protected-helper coverage seam.
    "tests/server/test_run_gc_background.py": {
        "protected-access",
    },
    # Stdio progress-driver test exercises ``_drive_stdio_progress``
    # and ``dispatch_tool`` — protected-helper coverage seam for the
    # MCP stdio in-band progress-notification path.
    "tests/server/test_stdio_progress.py": {
        "protected-access",
    },
    # Schema-derivation pin on the two resolver modules'
    # module-private ``_RESOLVER_JSON_SCHEMA`` constant — asserts the
    # candidate confidence bounds are derived from the Candidate model
    # so the two cannot drift; same protected-helper coverage seam.
    "tests/agents/test_resolver_schema_derivation.py": {
        "protected-access",
    },
}
ALLOWED_UUID4_CALLERS = {
    "src/mcp_server_phytomni/runtime/task_manager.py",
}


def test_python_file_names_follow_snake_case():
    """Verify python file names follow snake case."""
    root = Path(__file__).resolve().parents[2]
    bad_names = [
        path.relative_to(root).as_posix()
        for base in (root / "src", root / "tests")
        for path in base.rglob("*.py")
        if not PYTHON_FILE_PATTERN.fullmatch(path.name)
    ]

    assert bad_names == []


def test_python_files_use_standard_header_and_module_docstring():
    """Verify python files use standard header and module docstring."""
    root = Path(__file__).resolve().parents[2]
    failures = []

    for base in (root / "src", root / "tests"):
        for path in base.rglob("*.py"):
            lines = path.read_text(encoding="utf-8").splitlines()
            relative_path = path.relative_to(root).as_posix()
            if tuple(lines[:2]) != COPYRIGHT_HEADER:
                failures.append(f"{relative_path}: copyright header")
                continue

            author_lines = []
            for line in lines[2:]:
                if line.startswith("# Author: ") or line.startswith(
                    "#         "
                ):
                    author_lines.append(line)
                    continue
                break
            if not author_lines:
                failures.append(f"{relative_path}: author header")
                continue
            if not AUTHOR_FIRST_LINE_PATTERN.fullmatch(author_lines[0]):
                failures.append(f"{relative_path}: first author line")
            for line in author_lines[1:]:
                if not AUTHOR_CONTINUATION_PATTERN.fullmatch(line):
                    failures.append(
                        f"{relative_path}: continuation author line"
                    )

            docstring_index = 2 + len(author_lines)
            if docstring_index >= len(lines) or not lines[
                docstring_index
            ].startswith('"""'):
                failures.append(f"{relative_path}: immediate module docstring")
                continue

            module = ast.parse(path.read_text(encoding="utf-8"))
            module_docstring = ast.get_docstring(module)
            if not module_docstring:
                failures.append(f"{relative_path}: missing module docstring")
            elif len(module_docstring.splitlines()) > 8:
                failures.append(f"{relative_path}: long module docstring")

    assert not failures


def test_ruff_enforces_import_grouping_and_sorting():
    """Verify ruff enforces import grouping and sorting."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    ruff_lint = pyproject["tool"]["ruff"]["lint"]
    assert "I" in ruff_lint["extend-select"]
    assert pyproject["tool"]["ruff"]["lint"]["isort"] == {
        "known-first-party": [
            "mcp_client_phytomni",
            "mcp_server_phytomni",
        ]
    }


def test_flake8_uses_black_compatible_style_without_init_ignores():
    """Verify flake8 uses black compatible style without init ignores."""
    root = Path(__file__).resolve().parents[2]
    parser = configparser.ConfigParser()
    parser.read(root / ".flake8", encoding="utf-8")

    flake8_config = parser["flake8"]
    ignored_rules = {
        rule.strip()
        for rule in flake8_config["extend-ignore"].split(",")
        if rule.strip()
    }

    assert ignored_rules == {"E203", "W503"}
    assert "per-file-ignores" not in flake8_config


def test_function_docstring_waiver_is_removed_from_tests():
    """Verify function docstring waivers do not return to tests."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    pylint_disable = set(
        pyproject["tool"]["pylint"]
        .get("messages_control", {})
        .get("disable", [])
    )
    assert "missing-function-docstring" not in pylint_disable

    violations = []
    for path in (root / "tests").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if (
                PYLINT_DISABLE_MARKER in line
                and "missing-function-docstring" in line
            ):
                violations.append(path.relative_to(root).as_posix())

    assert not violations


def test_global_pylint_disables_are_not_reintroduced():
    """Verify global pylint disables are not reintroduced."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )

    pylint_disable = (
        pyproject["tool"]["pylint"]
        .get("messages_control", {})
        .get("disable", [])
    )

    assert pylint_disable == []


def test_local_pylint_disables_are_langgraph_boundary_only():
    """Verify local pylint disables stay limited to LangGraph boundaries."""
    root = Path(__file__).resolve().parents[2]
    violations = []

    for base in (root / "src", root / "tests"):
        for path in base.rglob("*.py"):
            relative_path = path.relative_to(root).as_posix()
            allowed_rules = ALLOWED_LOCAL_PYLINT_DISABLES.get(
                relative_path, set()
            )
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if PYLINT_DISABLE_MARKER not in line:
                    continue
                disabled_rules = {
                    rule.strip()
                    for rule in line.split(PYLINT_DISABLE_MARKER, 1)[1].split(
                        ","
                    )
                    if rule.strip()
                }
                if disabled_rules - allowed_rules:
                    violations.append(f"{relative_path}:{line_number}")

    assert not violations


def test_init_files_with_imports_declare_all():
    """Verify __init__.py modules with imports declare __all__.

    STYLE.md mandates that ``__init__.py`` re-exports are made explicit
    via ``__all__``. An ``__init__.py`` that imports from sibling modules
    is treated as performing re-exports, so it must declare ``__all__``.
    Empty or docstring-only ``__init__.py`` files are exempt.
    """
    root = Path(__file__).resolve().parents[2]
    violations = []

    for path in (root / "src").rglob("__init__.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        has_imports = any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            for node in module.body
        )
        if not has_imports:
            continue
        has_all = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
            for node in module.body
        )
        if not has_all:
            violations.append(path.relative_to(root).as_posix())

    assert not violations


def test_runtime_ids_do_not_use_direct_uuid_generation():
    """Verify runtime paths and threads use path_policy instead of UUIDs."""
    root = Path(__file__).resolve().parents[2]
    direct_uuid4_pattern = re.compile(r"(?<![.\w])uuid4\(")
    violations = []

    for path in (root / "src").rglob("*.py"):
        relative_path = path.relative_to(root).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if "uuid1(" in line or "from uuid import uuid1" in line:
                violations.append(f"{relative_path}:{line_number}: uuid1")
            if (
                "uuid.uuid4(" in line
                and relative_path not in ALLOWED_UUID4_CALLERS
            ):
                violations.append(f"{relative_path}:{line_number}: uuid4")
            if "from uuid import uuid4" in line:
                violations.append(
                    f"{relative_path}:{line_number}: uuid4 import"
                )
            if direct_uuid4_pattern.search(line):
                violations.append(
                    f"{relative_path}:{line_number}: direct uuid4"
                )

    assert not violations
