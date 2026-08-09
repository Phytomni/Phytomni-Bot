# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Safe configuration and evidence helpers for Research-input live e2e.

The suite intentionally does not publish fixtures or construct object paths.
An operator must provide the exact, already-approved synthetic objects through
the environment.  This prevents a manual acceptance run from accidentally
reading a customer object or making a real path part of test output.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

E2E_RESEARCH_INPUT_FLAG = "PHYTOMNI_E2E_RESEARCH_INPUT"
E2E_RESEARCH_INPUT_REFS = "PHYTOMNI_E2E_RESEARCH_INPUT_OBJECT_REFS"
E2E_RESEARCH_INPUT_TIMEOUT = "PHYTOMNI_E2E_RESEARCH_INPUT_TIMEOUT_SECONDS"
_REQUIRED_FLAGS = (
    "PHYTOMNI_RUN_INTEGRATION",
    "PHYTOMNI_ALLOW_NETWORK",
    E2E_RESEARCH_INPUT_FLAG,
)
_SYNTHETIC_REFERENCE_MARKER = "/phytomni-e2e/research-input/"
_EXPECTED_REFERENCE_COUNT = 11
_MAX_TIMEOUT_SECONDS = 1_800.0


@dataclass(frozen=True, slots=True)
class ResearchInputE2eConfig:
    """Validated, non-displayable input for the manually gated e2e run."""

    references: tuple[str, ...]
    timeout_seconds: float

    @property
    def reference_digest(self) -> str:
        """Return a safe digest identifying the configured fixture set."""
        value = "\n".join(self.references).encode("utf-8")
        return f"sha256:{hashlib.sha256(value).hexdigest()}"


def research_input_e2e_enabled() -> bool:
    """Return whether every explicit live-execution guard is enabled."""
    return all(os.environ.get(flag) == "1" for flag in _REQUIRED_FLAGS)


def load_research_input_e2e_config() -> ResearchInputE2eConfig:
    """Load exactly eleven approved synthetic object references.

    The marker is deliberately part of the object-key policy, not a bucket
    name: the configured development bucket remains deployment-specific while
    every referenced object is still visibly confined to the synthetic e2e
    namespace.
    """
    raw = os.environ.get(E2E_RESEARCH_INPUT_REFS)
    if raw is None:
        raise ValueError(
            f"{E2E_RESEARCH_INPUT_REFS} must contain a JSON array of "
            "approved synthetic references"
        )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{E2E_RESEARCH_INPUT_REFS} must be valid JSON"
        ) from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != _EXPECTED_REFERENCE_COUNT
    ):
        raise ValueError(
            f"{E2E_RESEARCH_INPUT_REFS} must contain exactly "
            f"{_EXPECTED_REFERENCE_COUNT} references"
        )
    references = tuple(decoded)
    if not all(
        isinstance(reference, str)
        and reference.startswith("obs://")
        and _SYNTHETIC_REFERENCE_MARKER in reference
        for reference in references
    ):
        raise ValueError(
            "Research e2e references must be synthetic obs:// objects in "
            "the phytomni-e2e/research-input namespace"
        )
    if len(set(references)) != len(references):
        raise ValueError("Research e2e references must be unique")
    return ResearchInputE2eConfig(
        references=references,
        timeout_seconds=_load_timeout_seconds(),
    )


def build_three_form_query(config: ResearchInputE2eConfig) -> str:
    """Build one request containing fenced, Tab, and terminal JSON forms."""
    fenced, standalone, trailing = (
        config.references[:4],
        config.references[4:7],
        config.references[7:],
    )
    fenced_json = json.dumps(
        {reference: "synthetic e2e dataset" for reference in fenced},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    trailing_json = json.dumps(
        {reference: "synthetic e2e dataset" for reference in trailing},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    standalone_lines = "\n".join(
        f"{reference}\tsynthetic e2e dataset" for reference in standalone
    )
    return (
        "Create one bounded synthetic research plan.\n"
        "data: ```json\n"
        f"{fenced_json}\n"
        "```\n"
        f"{standalone_lines}\n"
        f"data: {trailing_json}"
    )


def assert_redacted_evidence(
    value: object, config: ResearchInputE2eConfig
) -> None:
    """Assert public evidence contains no configured object reference."""
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    assert all(
        reference not in rendered for reference in config.references
    ), "public e2e evidence disclosed a configured object reference"


def sanitize_evidence(
    *, run_id: str, stages: tuple[str, ...], config: ResearchInputE2eConfig
) -> dict[str, Any]:
    """Return the only evidence shape safe to show in assertion failures."""
    return {
        "run_id": run_id,
        "reference_count": len(config.references),
        "reference_digest": config.reference_digest,
        "stages": stages,
    }


def _load_timeout_seconds() -> float:
    """Return a finite operator-overridable timeout for the one child run."""
    raw = os.environ.get(E2E_RESEARCH_INPUT_TIMEOUT)
    if raw is None:
        return _MAX_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{E2E_RESEARCH_INPUT_TIMEOUT} must be numeric"
        ) from exc
    if not 0 < timeout <= _MAX_TIMEOUT_SECONDS:
        raise ValueError(
            f"{E2E_RESEARCH_INPUT_TIMEOUT} must be in (0, "
            f"{_MAX_TIMEOUT_SECONDS}]"
        )
    return timeout
