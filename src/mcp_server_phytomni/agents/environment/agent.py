# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Environment agents for regional vegetation index analysis workflows.

This module exposes `region_vci_analysis` plus the public helpers
(``environment_region_codes`` / ``environment_output_dir`` /
``environment_submit_kwargs`` / ``environment_chat_kwargs``) the
LangGraph nodes in :mod:`.graph` call into. ``region_vci_analysis``
itself is now a thin wrapper that delegates to the compiled
environment subgraph via :func:`ainvoke_graph`.
"""

import importlib
import re
from functools import lru_cache
from typing import Any

from ...common.prompts import get_prompt, load_text_file
from ...config.defaults import EnvironmentConfig
from ...config.settings import get_sensitive_config
from ...graphs.chat_adapters import build_chat_input, extract_chat_response
from ...runtime.langgraph_runner import ainvoke_graph
from ...runtime.locale import SupportedLocale
from ...storage.path_policy import RunIdentity
from ..analyst.agent import submit
from ..chat.service import _cached_chat_app
from ..shared.analysis_storage import create_output_dir, get_data_list
from ..shared.options import (
    SubmitKwargsSpec,
    build_chat_kwargs,
    build_submit_kwargs,
    resolve_agent_locale,
)

ENVIRONMENT_CONFIG = EnvironmentConfig()

__all__ = [
    "ENVIRONMENT_CONFIG",
    "create_output_dir",
    "environment_chat_kwargs",
    "environment_output_dir",
    "environment_region_codes",
    "environment_submit_kwargs",
    "get_data_list",
    "get_prompt",
    "load_text_file",
    "region_vci_analysis",
    "submit",
]


def environment_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for environment code extraction."""
    return build_chat_kwargs(
        kwargs, ENVIRONMENT_CONFIG, get_sensitive_config()
    )


def environment_submit_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return Analyst submit kwargs for the environment workflow."""
    sensitive = get_sensitive_config()
    return build_submit_kwargs(
        kwargs,
        ENVIRONMENT_CONFIG,
        sensitive,
        sensitive.obs_credentials(),
        SubmitKwargsSpec(
            task_name="environment-agents-vci-task",
            compute_resource="large",
        ),
    )


async def environment_region_codes(
    query: str,
    kwargs: dict[str, Any],
) -> tuple[str | None, str | None, str | None] | None:
    """Return province, city, and county codes extracted from a query."""
    prompt_file = kwargs.get("prompt_file", ENVIRONMENT_CONFIG.PROMPT_FILE)
    region_info = load_text_file(
        kwargs.get("region_code", ENVIRONMENT_CONFIG.REGION_CODE)
    )
    prompt = get_prompt(
        prompt_file,
        "user/environment/get_code_query",
        {"json_dict": region_info, "query": query},
    )
    chat_kwargs_bag = environment_chat_kwargs(kwargs)
    chat_output = await _cached_chat_app().ainvoke(
        build_chat_input(user_query=prompt, chat_kwargs=chat_kwargs_bag)
    )
    phyto_response = extract_chat_response(chat_output)
    if not phyto_response:
        return None
    content = phyto_response["choices"][0]["message"]["content"]
    try:
        code_info = re.findall(r"<result>(.*?)</result>", content)[0]
    except IndexError:
        return None
    return tuple((code_info.split("|") + [None] * 3)[:3])


def environment_output_dir(user_id: str | None, kwargs: dict[str, Any]) -> str:
    """Create the output directory for a non-batch VCI task."""
    run_identity = RunIdentity.create(
        user_id=user_id,
        scope="vci_analysis_task",
    )
    default_access_key_id, default_secret_access_key = (
        get_sensitive_config().obs_credentials()
    )
    return create_output_dir(
        run_identity.user_id,
        "vci_analysis_task",
        access_key_id=kwargs.get("access_key_id", default_access_key_id),
        secret_access_key=kwargs.get(
            "secret_access_key", default_secret_access_key
        ),
        obs_server=kwargs.get("obs_server", ENVIRONMENT_CONFIG.OBS_SERVER),
        bucket_name=kwargs.get("bucket_name", ENVIRONMENT_CONFIG.BUCKET_NAME),
        run_identity=run_identity,
    )


@lru_cache(maxsize=1)
def _cached_environment_app() -> Any:
    """Lazy singleton of the compiled environment subgraph.

    Resolves ``builder`` dynamically via ``importlib.import_module``
    rather than a top-level ``from .builder import build_environment_graph``
    because the builder pulls ``graph.py`` which in turn re-imports
    this module for the helper namespace; deferring the resolve to
    first call lets every module finish loading before the compile
    runs. ``lru_cache`` makes the compile happen at most once and
    gives test suites a ``cache_clear()`` hook.
    """
    builder_module = importlib.import_module(".builder", package=__package__)
    return builder_module.build_environment_graph()


async def region_vci_analysis(
    query: str,
    batch: bool = False,
    *,
    locale: SupportedLocale | None = None,
    **kwargs: Any,
) -> dict:
    """Run a regional VCI analysis workflow and return task results.

    Delegates to the compiled environment subgraph: the
    ``extract_region_codes_node`` runs the chat extraction and
    ``submit_vci_task_node`` issues the analyst submission, with
    the conditional edge short-circuiting to END when region-code
    extraction yields no parseable result.

    Args:
        query: Natural-language region analysis request.
        batch: Whether to reuse the provided output directory.
        locale: Optional response locale.
        **kwargs: Keyword-compatible chat, OBS, and submit overrides.

    Returns:
        Dictionary containing the submitted VCI analysis task, or
        ``{"vci_analysis_task": None}`` when region code extraction
        fails.
    """
    initial_state: dict[str, Any] = {
        "query": query,
        "batch": batch,
        "locale": resolve_agent_locale(locale),
        "kwargs": kwargs,
    }
    final_state = await ainvoke_graph(_cached_environment_app(), initial_state)
    return {"vci_analysis_task": final_state.get("vci_analysis_task")}
