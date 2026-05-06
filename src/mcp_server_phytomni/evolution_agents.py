# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Evolution analysis helpers built on Phytomni analyst workflows."""

from json import loads
from typing import Any, Dict, List
from uuid import uuid1

import requests

from .analyst_agents import create_output_dir, get_data_list, submit
from .chat_agents import phyto_chat
from .config.defaults import DeepGenomeConfig
from .config.settings import SensitiveConfig
from .utils import get_prompt, get_token

DEEP_GENOME_CONFIG = DeepGenomeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)
_manager_cache: Dict[str, Any] = {}


def _resource_dict(value: Any, default: dict) -> dict:
    """Return a copied nested resource dictionary."""
    source = default if value is None else value
    return {key: dict(item) for key, item in source.items()}


def _evolution_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for evolution target-species extraction."""
    retriable_codes = kwargs.get("retriable_codes")
    return {
        "prompt_file": kwargs.get(
            "prompt_file", DEEP_GENOME_CONFIG.PROMPT_FILE
        ),
        "prompt_path": kwargs.get(
            "prompt_path", DEEP_GENOME_CONFIG.PROMPT_PATH
        ),
        "api_key": kwargs.get(
            "api_key", SENSITIVE_CONFIG.API_KEY.get_secret_value()
        ),
        "base_url": kwargs.get("base_url", SENSITIVE_CONFIG.BASE_URL),
        "model": kwargs.get("model", SENSITIVE_CONFIG.MODEL_ID),
        "frequency_penalty": kwargs.get(
            "frequency_penalty", DEEP_GENOME_CONFIG.FREQUENCY_PENALTY
        ),
        "n": kwargs.get("n", DEEP_GENOME_CONFIG.N),
        "presence_penalty": kwargs.get(
            "presence_penalty", DEEP_GENOME_CONFIG.PRESENCE_PENALTY
        ),
        "reasoning_effort": kwargs.get(
            "reasoning_effort", DEEP_GENOME_CONFIG.REASONING_EFFORT
        ),
        "stream": kwargs.get("stream", DEEP_GENOME_CONFIG.STREAM),
        "temperature": kwargs.get(
            "temperature", DEEP_GENOME_CONFIG.TEMPERATURE
        ),
        "top_p": kwargs.get("top_p", DEEP_GENOME_CONFIG.TOP_P),
        "user": kwargs.get("user", DEEP_GENOME_CONFIG.USER),
        "timeout": kwargs.get("timeout", DEEP_GENOME_CONFIG.TIMEOUT),
        "retriable_codes": (
            list(DEEP_GENOME_CONFIG.RETRIABLE_CODES)
            if retriable_codes is None
            else list(retriable_codes)
        ),
        "max_retries": kwargs.get(
            "max_retries", DEEP_GENOME_CONFIG.MAX_RETRIES
        ),
    }


def _evolution_submit_kwargs(
    kwargs: dict[str, Any],
    enable_auto_select: bool,
) -> dict[str, Any]:
    """Return Analyst submit kwargs for the evolution workflow."""
    return {
        "is_create_dir": False,
        "execute_code": True,
        "enable_auto_select": enable_auto_select,
        "model_url": kwargs.get("model_url", SENSITIVE_CONFIG.CODER_URL),
        "model_name": kwargs.get("model_name", SENSITIVE_CONFIG.CODER_MODEL),
        "coder_api_key": kwargs.get(
            "coder_api_key", SENSITIVE_CONFIG.CODER_API_KEY.get_secret_value()
        ),
        "access_key_id": kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        "secret_access_key": kwargs.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        "obs_server": kwargs.get("obs_server", DEEP_GENOME_CONFIG.OBS_SERVER),
        "bucket_name": kwargs.get(
            "bucket_name", DEEP_GENOME_CONFIG.BUCKET_NAME
        ),
        "analysis_url": kwargs.get(
            "analysis_url", DEEP_GENOME_CONFIG.ANALYSIS_URL
        ),
        "region": kwargs.get("region", DEEP_GENOME_CONFIG.ANALYSIS_REGION),
        "task_name": "evolution-agents-evo-task",
        "resource_dict": _resource_dict(
            kwargs.get("resource_dict"), DEEP_GENOME_CONFIG.RESOURCE
        ),
        "app_id_dict": dict(
            kwargs.get("app_id_dict") or DEEP_GENOME_CONFIG.APP_ID
        ),
        "compute_resource": "medium",
        "timeout": kwargs.get("timeout", DEEP_GENOME_CONFIG.TIMEOUT),
        "retriable_codes": _evolution_chat_kwargs(kwargs)["retriable_codes"],
        "max_retries": kwargs.get(
            "max_retries", DEEP_GENOME_CONFIG.MAX_RETRIES
        ),
        "max_poll": kwargs.get("max_poll", DEEP_GENOME_CONFIG.MAX_POLL),
    }


async def _find_spa_taxids(spa_names: str, timeout: float) -> List[str]:
    """Return taxonomy ids for a target species name."""
    repo_id = "4a533117-9416-4e8b-b7cc-27b448a90095"
    endpoint = "http://1.95.74.240:8000"
    url = f"{endpoint}/v1/koosearch/repos/{repo_id}/faqs"
    headers = {
        "X-Auth-Token": await get_token(),
        "Content-Type": "application/json",
    }
    disabled_proxies: Any = {"http": None, "https": None}
    response = requests.get(
        url,
        headers=headers,
        params={"question": spa_names, "page_size": 10, "page_num": 1},
        proxies=disabled_proxies,
        timeout=timeout,
    )
    if response.status_code != 200:
        return []
    response_taxid_data = response.json()
    if response_taxid_data["total"] <= 0:
        return []
    return [
        faq["answer"].split(".")[0] for faq in response_taxid_data["records"]
    ]


async def _target_taxids(query: str, kwargs: dict[str, Any]) -> str | None:
    """Extract target taxonomy ids for an evolution query."""
    prompt_file = kwargs.get("prompt_file", DEEP_GENOME_CONFIG.PROMPT_FILE)
    prompt = get_prompt(
        prompt_file,
        "user/get_taxid_meta",
        {"user_query": query},
    )
    phyto_response = await phyto_chat(
        user_query=prompt,
        **_evolution_chat_kwargs(kwargs),
    )
    if phyto_response is None:
        return None
    content = phyto_response["choices"][0]["message"]["content"]
    target_spa_list = loads(content.replace("'", '"'))
    targets = target_spa_list["target_spa_list"]
    if targets[0] == "All":
        return "All"
    taxid_lists = [
        await _find_spa_taxids(
            spa,
            kwargs.get("timeout", DEEP_GENOME_CONFIG.TIMEOUT),
        )
        for spa in targets
    ]
    return ",".join(taxid for taxids in taxid_lists for taxid in taxids)


def _evolution_output_dir(user_id: str | None, kwargs: dict[str, Any]) -> str:
    """Create the output directory for a non-batch evolution task."""
    return create_output_dir(
        user_id=user_id or str(uuid1()),
        task="evolution_agents_task",
        access_key_id=kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        secret_access_key=kwargs.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        obs_server=kwargs.get("obs_server", DEEP_GENOME_CONFIG.OBS_SERVER),
        bucket_name=kwargs.get("bucket_name", DEEP_GENOME_CONFIG.BUCKET_NAME),
    )


async def evo_test_analysis(
    query: str,
    species: str,
    gene_id: str,
    batch: bool = False,
    enable_auto_select: bool = False,
    **kwargs: Any,
) -> dict:
    """Run an evolution analysis workflow for a target gene."""
    user_id = kwargs.get("user_id", DEEP_GENOME_CONFIG.USER_ID)
    prompt_file = kwargs.get("prompt_file", DEEP_GENOME_CONFIG.PROMPT_FILE)
    deepgenome_data = kwargs.get(
        "deepgenome_data", DEEP_GENOME_CONFIG.DEEPGENOME_DATA
    )
    output_dir = kwargs.get("output_dir", DEEP_GENOME_CONFIG.OUTPUT_DIR)
    target_spa_taxids = await _target_taxids(query, kwargs)
    if target_spa_taxids is None:
        return {"evolution_task": None}
    goal_description = get_prompt(
        prompt_file,
        "user/evolution_agents_analysis",
        {"gene_id": gene_id, "target_taxid": target_spa_taxids},
    )
    data_list = get_data_list(deepgenome_data, "evolution_analysis", species)
    if not batch:
        output_dir = _evolution_output_dir(user_id, kwargs)
    meta = get_prompt(prompt_file, "user/evolution_agents_meta")
    evo_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        output_dir=output_dir,
        meta=meta,
        **_evolution_submit_kwargs(kwargs, enable_auto_select),
    )
    return {"evolution_agents_task": evo_task}
