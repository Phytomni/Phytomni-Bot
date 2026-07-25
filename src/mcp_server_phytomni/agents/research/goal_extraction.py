# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chat-backed extraction of in-silico research goals."""

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
from .contracts import ResearchGoalBatch

PromptBuilder = Callable[..., str]
ChatAppFactory = Callable[[], Any]


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
            dependencies.sensitive_config,
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
    batch = ResearchGoalBatch.model_validate(
        loads(phyto_response["choices"][0]["message"]["content"])
    )
    return batch.as_dicts()
