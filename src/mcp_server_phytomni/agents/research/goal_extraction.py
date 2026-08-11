# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-backed extraction of in-silico research goals.

The evidence-facing seam consumes an already extracted immutable evidence
object.  The legacy query/path wrapper remains for MCP and graph callers
whose older contract still owns document-context download.
"""

from collections.abc import Callable
from json import loads
from typing import Any, NamedTuple

from ...config.defaults import InSilicoResearchConfig
from ...config.settings import SensitiveConfig
from ...graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
    extract_chat_response,
)
from ...runtime.locale import SupportedLocale
from ...storage.downloads import download_upload_context
from .contracts import ResearchGoal, ResearchGoalBatch
from .document_evidence import ExtractedResearchEvidence
from .planning import research_planning_failure

PromptBuilder = Callable[..., str]
ChatAppFactory = Callable[[], Any]
MAX_GOAL_EVIDENCE_CHARS = 131_072

__all__ = [
    "MAX_GOAL_EVIDENCE_CHARS",
    "ResearchGoalExtractionDependencies",
    "extract_research_goals",
    "extract_research_goals_from_evidence",
]

_goal_extraction_failure = research_planning_failure


class ResearchGoalExtractionDependencies(NamedTuple):
    """Runtime seams needed to extract research goals."""

    in_silico_config: InSilicoResearchConfig
    sensitive_config: SensitiveConfig
    prompt_builder: PromptBuilder
    chat_app_factory: ChatAppFactory


async def extract_research_goals(
    user_query: str,
    obs_file_list: list[str],
    *,
    locale: SupportedLocale | None,
    dependencies: ResearchGoalExtractionDependencies,
) -> list[dict[str, str]]:
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
    return [
        {"goal": goal.goal, "context": goal.context or ""} for goal in goals
    ]


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
                            "maxLength": 1000,
                        },
                        "context": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4000,
                        },
                    },
                    "required": ["goal"],
                },
            },
        },
        locale=locale,
    )
    chat_output = await dependencies.chat_app_factory().ainvoke(
        build_chat_input(user_query=user_query, chat_kwargs=chat_kwargs)
    )
    phyto_response = extract_chat_response(chat_output)
    if not phyto_response:
        raise ValueError("research goal extraction returned no goals")
    try:
        batch = ResearchGoalBatch.model_validate(
            loads(phyto_response["choices"][0]["message"]["content"])
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise research_planning_failure() from error
    return tuple(batch.root)


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
        part = f"[evidence_{ordinal + 1:03d}]\n{unit.text.strip()}"
        total += len(part) + (2 if parts else 0)
        if total > MAX_GOAL_EVIDENCE_CHARS:
            raise _goal_extraction_failure()
        parts.append(part)
    return "\n\n".join(parts)
