# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared assertion constants and markdown helpers for e2e tests.

Centralizes the keyword/cue tuples and the citation-envelope markdown
unwrap + section counter so the MCP and HTTP e2e suites assert against
one source instead of cross-file duplicated literals.
"""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import ZipFile

from mcp_server_phytomni.common.responses import assert_no_citation_residue
from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.storage.obs_client import ObsClient
from mcp_server_phytomni.storage.obs_relay_ops import (
    ObsAccessOptions,
    get_object_bytes,
)

from .polling import TaskState

PHOTOSYNTHESIS_KEYWORDS = ("photosynthesis", "c3", "calvin", "rubisco")
WHEAT_DROUGHT_KEYWORDS = (
    "drought",
    "wheat",
    "triticum",
    "aba",
    "dreb",
    "snrk",
)
GENE_ID = "Os01g0177400"
ANNOTATION_CUES = (
    "function",
    "expression",
    "ortholog",
    "homolog",
    "domain",
    "pathway",
    "literature",
    "tissue",
)
MIN_REVIEW_SECTIONS = 3

# A markdown section header: an ATX heading of depth >= 2 (## .. ######).
# Depth >= 2 (not a literal "## ") because the drafting LLM varies the
# level it uses for sections run-to-run (## vs ###) while reserving a
# single # / ## for the document title; counting any sub-title heading
# captures "the multi-section path ran" without being brittle to that
# variation, yet still yields 0 for an empty or unstructured answer.
ATX_SECTION = re.compile(r"^\s{0,3}#{2,6}\s+\S")


def markdown_body(answer: str) -> str:
    """Return the markdown body from an answer string.

    Cited agents now emit markdown with inline ``<sup>N</sup>`` citation
    markers in ``message.content`` (citation documents live on the
    top-level ``references`` field). Earlier server builds wrapped the
    answer as a ``{"content": "<markdown>", "doc_list": [...]}`` JSON
    envelope, so this helper keeps a defensive unwrap to stay
    compatible with archived e2e logs and any client still talking to
    a pre-unwrap server.

    Args:
        answer: Raw answer / message-content string.

    Returns:
        The markdown body to scan for section headers.
    """
    try:
        parsed = json.loads(answer)
    except (ValueError, TypeError):
        return answer
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        return parsed["content"]
    return answer


def section_count(answer: str) -> int:
    """Count depth>=2 ATX markdown headers in a possibly wrapped answer.

    Unwraps the citation envelope via ``markdown_body`` then counts
    depth>=2 ATX headings (see ``ATX_SECTION``).

    Args:
        answer: Raw answer / message-content string.

    Returns:
        Number of depth>=2 ATX section headers found.
    """
    body = markdown_body(answer)
    return sum(1 for line in body.splitlines() if ATX_SECTION.match(line))


def assert_chat_answer(answer: str) -> None:
    """Assert ChatAgent answer mentions a photosynthesis-related keyword.

    Args:
        answer: Raw answer / message-content string from ChatAgent.

    Raises:
        AssertionError: When the answer is empty or lacks every cue.
    """
    assert answer, "ChatAgent answer was empty"
    lowered = answer.lower()
    matched = [kw for kw in PHOTOSYNTHESIS_KEYWORDS if kw in lowered]
    assert matched, (
        f"ChatAgent answer missed every expected keyword "
        f"({PHOTOSYNTHESIS_KEYWORDS}); got: {answer!r}"
    )


def assert_knowledge_answer(answer: str) -> None:
    """Assert KnowledgeAgent answer cites wheat drought cue words.

    Args:
        answer: Raw answer / message-content string from KnowledgeAgent.

    Raises:
        AssertionError: When the answer is empty or lacks every cue.
    """
    assert answer, "KnowledgeAgent answer was empty"
    _assert_no_citation_residue_via_markdown_body(answer)
    lowered = answer.lower()
    matched = [kw for kw in WHEAT_DROUGHT_KEYWORDS if kw in lowered]
    assert matched, (
        f"KnowledgeAgent answer missed every expected keyword "
        f"({WHEAT_DROUGHT_KEYWORDS}); got: {answer!r}"
    )


_DATA_SUMMARY_PATTERN = re.compile(r"^(\d+) rows? x (\d+) columns?$")


def assert_data_answer(answer: str) -> None:
    """Assert DataAgent answer is a non-empty tabular summary.

    The DataAgent formatter now emits a human-readable ``N rows x M
    columns`` summary on ``answer`` and ships the actual tabular
    payload through ``formatted.tabular`` instead of JSON-encoding it
    inside the answer string. A real SQL backend call must produce at
    least one row and one column; the substring cues (gene_id,
    homolog, sequence, ...) live inside ``tabular.rows`` and are not
    reachable from this answer-string-only signature.

    Args:
        answer: Raw answer string from DataAgent
            (``formatted.answer`` from the envelope).

    Raises:
        AssertionError: When the answer is empty, does not match the
            summary pattern, or reports zero rows / zero columns.
    """
    assert answer, "DataAgent answer was empty"
    match = _DATA_SUMMARY_PATTERN.match(answer.strip())
    assert match, (
        "DataAgent answer is not the new ``N rows x M columns`` summary; "
        f"got: {answer!r}"
    )
    rows = int(match.group(1))
    cols = int(match.group(2))
    assert rows > 0, f"DataAgent returned 0 rows; got: {answer!r}"
    assert cols > 0, f"DataAgent returned 0 columns; got: {answer!r}"


def _assert_no_citation_residue_via_markdown_body(answer: str) -> None:
    """Apply Align-A citation residue scanner to the unwrapped body.

    Unwraps any legacy ``{content, doc_list}`` JSON envelope via
    ``markdown_body`` first, then runs the shared
    ``assert_no_citation_residue`` from ``common.responses``.
    """
    assert_no_citation_residue(markdown_body(answer))


def assert_review_answer(answer: str) -> None:
    """Assert ReviewAgent answer has the multi-section drafting shape.

    Args:
        answer: Raw answer string from ReviewAgent.

    Raises:
        AssertionError: When the answer is empty or has too few
            depth>=2 markdown section headers.
    """
    assert answer, "ReviewAgent answer was empty"
    _assert_no_citation_residue_via_markdown_body(answer)
    count = section_count(answer)
    assert count >= MIN_REVIEW_SECTIONS, (
        f"ReviewAgent answer had only {count} markdown section "
        f"headers (expected >= {MIN_REVIEW_SECTIONS}); got: {answer!r}"
    )


def assert_brief_gene_answer(answer: str) -> None:
    """Assert BriefGeneAgent returned the rich preamble for the canonical id.

    Args:
        answer: Raw answer string from BriefGeneAgent.

    Raises:
        AssertionError: When the answer is empty, omits the gene id,
            lacks every annotation cue, or is missing the preamble
            structure (``## Gene Profiles`` + ``### Basic Genomic
            Information`` + all four ``### 1.``-``### 4.`` analytical
            sections).
    """
    assert answer, "BriefGeneAgent answer was empty"
    _assert_no_citation_residue_via_markdown_body(answer)
    lowered = answer.lower()
    assert GENE_ID.lower() in lowered, (
        f"BriefGeneAgent answer did not mention {GENE_ID}; " f"got: {answer!r}"
    )
    matched = [cue for cue in ANNOTATION_CUES if cue in lowered]
    assert matched, (
        f"BriefGeneAgent answer lacked every annotation cue "
        f"({ANNOTATION_CUES}); got: {answer!r}"
    )
    assert "## Gene Profiles" in answer, (
        "BriefGeneAgent answer lacked the '## Gene Profiles' preamble "
        f"header; got: {answer!r}"
    )
    assert "### Basic Genomic Information" in answer, (
        "BriefGeneAgent answer lacked the Basic Genomic Information "
        f"block; got: {answer!r}"
    )
    missing_sections = [n for n in (1, 2, 3, 4) if f"### {n}." not in answer]
    assert not missing_sections, (
        f"BriefGeneAgent answer was missing analytical section(s) "
        f"{missing_sections} (expected all of ### 1.-### 4.); "
        f"got: {answer!r}"
    )


def assert_remote_run_terminal_payload(
    result: dict[str, Any], *, agent: str
) -> dict[str, Any]:
    """Assert a terminal remote run carries one ready result archive.

    Fire-and-forget agents (research / design / network / analyst) have no
    in-process completion stage; the HTTP run-level surface
    (``GET /v1/runs/{id}``) assembles a thin ``formatted.answer`` and
    concrete ``artifacts[].paths`` once at the settle transition. This
    pins both on a result polled to terminal.

    Args:
        result: The ``result`` object from a terminal ``/v1/runs/{id}``
            response.
        agent: Canonical result-producing Agent slug.

    Returns:
        The validated public archive descriptor.

    Raises:
        AssertionError: When the answer or canonical delivery is invalid.
    """
    formatted = result.get("formatted") or {}
    assert formatted.get("answer"), "terminal run carried no visible answer"
    execution = result.get("execution")
    assert isinstance(execution, dict), "terminal run carried no execution"
    delivery = execution.get("delivery")
    assert isinstance(delivery, dict), "terminal run carried no delivery"
    assert delivery.get("status") == "ready", "archive delivery was not ready"
    archive = delivery.get("archive")
    assert isinstance(archive, dict), "ready delivery carried no archive"
    assert archive.get("role") == "result_archive"
    assert archive.get("name") == f"{agent}-results.zip"
    size_bytes = archive.get("size_bytes")
    assert (
        isinstance(size_bytes, int)
        and not isinstance(size_bytes, bool)
        and size_bytes > 0
    ), "result archive size was not positive"
    return archive


def fetch_authenticated_result_archive(
    run_record: dict[str, Any],
    *,
    authenticated_user: str,
    agent: str,
) -> bytes:
    """Fetch one archive after pinning the owner-scoped public record.

    The live test enters through an authenticated HTTP read, then this
    Bot-side helper reconstructs the private digest-addressed object key.
    Browser-facing code never receives the key or archive bytes.
    """
    assert run_record.get("user_id") == authenticated_user
    assert run_record.get("agent") == agent
    result = run_record.get("result")
    assert isinstance(result, dict)
    archive = assert_remote_run_terminal_payload(result, agent=agent)
    execution = result["execution"]
    output_dirs = execution.get("output_dirs")
    assert isinstance(output_dirs, list) and len(output_dirs) == 1
    run_root = output_dirs[0]
    assert isinstance(run_root, str) and run_root
    path = PurePosixPath(run_root)
    assert path.is_absolute() and ".." not in path.parts

    delivery = execution["delivery"]
    digest = delivery.get("inventory_digest")
    assert isinstance(digest, str) and digest.startswith("sha256:")
    digest_hex = digest.removeprefix("sha256:")
    assert len(digest_hex) == 64 and all(
        character in "0123456789abcdef" for character in digest_hex
    )
    object_ref = f"{path}/delivery/{digest_hex}/{archive['name']}"
    config = ServerConfig()
    return _read_obs_object(
        config.BUCKET_NAME,
        object_ref,
        server=config.OBS_SERVER,
    )


def _read_obs_object(bucket: str, object_ref: str, *, server: str) -> bytes:
    """Read one E2E object through a short-lived authenticated SDK client."""
    access_key, secret_key = SensitiveConfig.load().obs_credentials()
    client = ObsClient(
        access_key_id=access_key,
        secret_access_key=secret_key,
        server=server,
    )
    try:
        return get_object_bytes(
            bucket,
            object_ref,
            access=ObsAccessOptions(client=client),
        )
    finally:
        client.close()


def assert_result_archive_members(content: bytes) -> None:
    """Require summary markdown plus one admitted scientific ZIP member."""
    assert content, "result archive was empty"
    with ZipFile(BytesIO(content)) as archive:
        members = tuple(
            info.filename for info in archive.infolist() if not info.is_dir()
        )
    assert "summary.md" in members, "result archive omitted summary.md"
    assert any(
        name.startswith("results/part-") for name in members
    ), "result archive omitted scientific result members"


def assert_deep_genome_terminal(state: TaskState) -> None:
    """Assert the DeepGenome terminal report state machine.

    BriefGene is the required profile. Its failure legitimately produces no
    report; every later failure must preserve the latest intermediate report.
    A successful umbrella requires a final report and a positive revision.

    Args:
        state: Sanitized state returned by the E2E polling helper.

    Raises:
        AssertionError: When the state violates the public report contract.
    """
    status = state.status.lower()
    assert status in {
        "succeeded",
        "failed",
    }, f"DeepGenome did not reach a terminal status: {state!r}"
    assert (
        state.report_revision >= 1
    ), f"DeepGenome terminal state had no report revision: {state!r}"
    if status == "succeeded":
        assert (
            state.final_report and state.final_report.strip()
        ), f"DeepGenome success carried no final report: {state!r}"
        assert state.report_stage == "final", state
        assert state.report_completeness in {"partial", "complete"}, state
        return

    if state.brief_gene_status.lower() == "failed":
        assert state.intermediate_report is None, state
        assert state.final_report is None, state
        assert state.report_stage == "waiting_for_brief_gene", state
        assert state.report_completeness == "none", state
        return

    assert state.final_report is None, state
    assert state.intermediate_report and state.intermediate_report.strip(), (
        "post-profile DeepGenome failure lost its intermediate report: "
        f"{state!r}"
    )
    assert state.report_stage == "intermediate", state
    assert state.report_completeness == "partial", state


def assert_terminal_report_and_artifacts(
    state: TaskState, *, needs_artifacts: bool
) -> None:
    """Assert terminal reports and, when required, concrete output paths.

    Args:
        state: Sanitized task snapshot from a live poll.
        needs_artifacts: Whether the agent contract requires output paths.

    Raises:
        AssertionError: When the task is not successful or lacks required
            report/artifact evidence.
    """
    assert state.succeeded, f"task did not succeed: {state!r}"
    assert (
        state.final_report and state.final_report.strip()
    ), f"successful task carried no final report: {state!r}"
    if not needs_artifacts:
        return
    has_artifact_paths = any(
        bool(item.get("paths")) for item in state.artifacts
    )
    assert (
        has_artifact_paths or state.output_dirs
    ), f"successful task carried no artifact/output paths: {state!r}"
