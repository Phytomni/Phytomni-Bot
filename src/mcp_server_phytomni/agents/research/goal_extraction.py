# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-backed extraction of in-silico research goals.

The evidence-facing seam consumes an already extracted immutable evidence
object.  The legacy query/path wrapper remains for MCP and graph callers
whose older contract still owns document-context download.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from json import JSONDecodeError, loads
from typing import Any, NamedTuple

from pydantic import ValidationError

from ...config.defaults import InSilicoResearchConfig
from ...config.settings import SensitiveConfig
from ...graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from ...runtime.locale import SupportedLocale
from ...storage.downloads import download_upload_context
from .contracts import (
    MAX_RESEARCH_GOAL_CHARS,
    MAX_RESEARCH_GOAL_CONTEXT_CHARS,
    ResearchGoal,
    ResearchGoalBatch,
)
from .document_evidence import ExtractedResearchEvidence
from .input_contracts import ResearchInputFailure, research_input_failure
from .planning import research_planning_failure

PromptBuilder = Callable[..., str]
ChatAppFactory = Callable[[], Any]
MAX_GOAL_EVIDENCE_CHARS = 131_072
logger = logging.getLogger(__name__)

__all__ = [
    "MAX_GOAL_EVIDENCE_CHARS",
    "EvidenceGoalProvider",
    "ResearchGoalExtractionDependencies",
    "extract_research_goals",
    "extract_research_goals_from_evidence",
]

_goal_extraction_failure = research_planning_failure
_PARSE_ERRORS = (
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    JSONDecodeError,
    ValidationError,
)


def _goal_chat_failure() -> ResearchInputFailure:
    """Public, retry-exhausted goal-extraction failure."""
    return research_input_failure(
        "research_goal_extraction_failed",
        (
            "Research goals could not be parsed from the paper. "
            "Please try again later."
        ),
        http_status_hint=422,
        retryable=False,
        stage="planning",
        last_stage="planning",
    )


class ResearchGoalExtractionDependencies(NamedTuple):
    """Runtime seams needed to extract research goals."""

    in_silico_config: InSilicoResearchConfig
    sensitive_config: SensitiveConfig
    prompt_builder: PromptBuilder
    chat_app_factory: ChatAppFactory


@dataclass(frozen=True, slots=True)
class EvidenceGoalProvider:
    """ResearchGoalProvider that extracts from retained evidence."""

    dependencies: ResearchGoalExtractionDependencies

    @property
    def contract_name(self) -> str:
        """Identify the evidence-backed goal-provider contract."""
        return "research_goal_provider"

    async def extract(
        self,
        evidence: ExtractedResearchEvidence,
        locale: SupportedLocale | None,
    ) -> tuple[ResearchGoal, ...]:
        """Return extractor-ordered goals without rewriting the query."""
        goals = await extract_research_goals_from_evidence(
            evidence,
            locale=locale,
            dependencies=self.dependencies,
        )
        logger.info("Extracted %d research goals", len(goals))
        return goals


async def extract_research_goals(
    user_query: str,
    obs_file_list: list[str],
    *,
    locale: SupportedLocale | None,
    dependencies: ResearchGoalExtractionDependencies,
) -> list[dict[str, Any]]:
    """Extract and validate research goals through the shared chat seam."""
    config = dependencies.in_silico_config
    if obs_file_list:
        upload_context, _ = await download_upload_context(
            obs_file_list,
            config,
        )
        user_query = dependencies.prompt_builder(
            config.PROMPT_FILE,
            "user/in_silico_research_goals_file",
            {"upload_context": upload_context, "paper_text": user_query},
        )
    else:
        user_query = dependencies.prompt_builder(
            config.PROMPT_FILE,
            "user/in_silico_research_goals",
            {"paper_text": user_query},
        )

    goals = await _extract_goals_from_prompt(
        user_query,
        locale=locale,
        dependencies=dependencies,
    )
    payload: list[dict[str, Any]] = []
    for goal in goals:
        item: dict[str, Any] = {
            "goal": goal.goal,
            "context": goal.context or "",
        }
        if goal.dataset_ids is not None:
            item["dataset_ids"] = list(goal.dataset_ids)
        payload.append(item)
    return payload


async def extract_research_goals_from_evidence(
    evidence: ExtractedResearchEvidence,
    *,
    locale: SupportedLocale | None,
    dependencies: ResearchGoalExtractionDependencies,
) -> tuple[ResearchGoal, ...]:
    """Extract goals from evidence without downloading or converting files."""
    evidence_text = _evidence_prompt(evidence)
    config = dependencies.in_silico_config
    prompt = dependencies.prompt_builder(
        config.PROMPT_FILE,
        "user/in_silico_research_goals",
        {"paper_text": evidence_text},
    )
    if not isinstance(prompt, str) or len(prompt) > MAX_GOAL_EVIDENCE_CHARS:
        raise research_planning_failure()
    return await _extract_goals_from_prompt(
        prompt,
        locale=locale,
        dependencies=dependencies,
    )


async def _extract_goals_from_prompt(
    user_query: str,
    *,
    locale: SupportedLocale | None,
    dependencies: ResearchGoalExtractionDependencies,
) -> tuple[ResearchGoal, ...]:
    """Call the shared chat seam and validate its bounded goal response."""
    config = dependencies.in_silico_config
    chat_kwargs = build_chat_kwargs_for(
        config,
        dependencies.sensitive_config,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "description": (
                    "A list of research objectives derived from the paper."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "goal": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_RESEARCH_GOAL_CHARS,
                        },
                        "context": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_RESEARCH_GOAL_CONTEXT_CHARS,
                        },
                        "dataset_ids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            },
                            "maxItems": 256,
                        },
                    },
                    "required": ["goal"],
                },
            },
        },
        locale=locale,
    )
    last_error: BaseException | None = None
    for _attempt in range(2):
        chat_output = await dependencies.chat_app_factory().ainvoke(
            build_chat_input(user_query=user_query, chat_kwargs=chat_kwargs)
        )
        phyto_response = extract_chat_response(chat_output)
        content: object = None
        try:
            content = phyto_response["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("goal content is not JSON text")
            batch = ResearchGoalBatch.model_validate(loads(content))
        except _PARSE_ERRORS as error:
            last_error = error
            parsed: object = None
            if isinstance(content, str):
                try:
                    parsed = loads(content)
                except (TypeError, ValueError, JSONDecodeError):
                    parsed = None
            logger.info(
                "goal extraction failed content_len=%s json_array=%s err=%s",
                len(content) if isinstance(content, str) else 0,
                isinstance(parsed, list),
                type(error).__name__,
            )
            continue
        return tuple(batch.root)
    raise _goal_chat_failure() from last_error


def _evidence_prompt(evidence: ExtractedResearchEvidence) -> str:
    """Serialize all retained evidence units into one bounded prompt."""
    if not isinstance(evidence, ExtractedResearchEvidence):
        raise _goal_extraction_failure()
    if not isinstance(evidence.units, tuple) or not evidence.units:
        raise _goal_extraction_failure()
    parts: list[str] = []
    total = 0
    for ordinal, unit in enumerate(evidence.units):
        if (
            not isinstance(unit.evidence_id, str)
            or not unit.evidence_id.strip()
            or not isinstance(unit.text, str)
            or not unit.text.strip()
        ):
            raise _goal_extraction_failure()
        chunks = [f"[evidence_{ordinal + 1:03d}]"]
        if unit.dataset_ids:
            chunks.append("dataset_ids: " + ", ".join(unit.dataset_ids))
        chunks.append(unit.text.strip())
        part = "\n".join(chunks)
        total += len(part) + (2 if parts else 0)
        if total > MAX_GOAL_EVIDENCE_CHARS:
            raise _goal_extraction_failure()
        parts.append(part)
    return "\n\n".join(parts)
