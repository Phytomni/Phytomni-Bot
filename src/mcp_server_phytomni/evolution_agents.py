# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Evolution analysis helpers built on Phytomni analyst workflows."""

from json import loads
from typing import Any, Dict, List

import requests

from .agents.analyst.agent import submit
from .agents.analyst.storage import create_output_dir, get_data_list
from .agents.chat.service import phyto_chat
from .agents.shared.options import (
    SubmitKwargsSpec,
    build_chat_kwargs,
    build_submit_kwargs,
)
from .auth.iam import get_token
from .config.defaults import DeepGenomeConfig
from .config.settings import SensitiveConfig
from .storage.path_policy import RunIdentity
from .utils import get_prompt

DEEP_GENOME_CONFIG = DeepGenomeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY = (
    SENSITIVE_CONFIG.obs_credentials()
)
_manager_cache: Dict[str, Any] = {}


def _evolution_chat_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return chat kwargs for evolution target-species extraction."""
    return build_chat_kwargs(kwargs, DEEP_GENOME_CONFIG, SENSITIVE_CONFIG)


def _evolution_submit_kwargs(
    kwargs: dict[str, Any],
    enable_auto_select: bool,
) -> dict[str, Any]:
    """Return Analyst submit kwargs for the evolution workflow."""
    return build_submit_kwargs(
        kwargs,
        DEEP_GENOME_CONFIG,
        SENSITIVE_CONFIG,
        (DEFAULT_ACCESS_KEY_ID, DEFAULT_SECRET_ACCESS_KEY),
        SubmitKwargsSpec(
            task_name="evolution-agents-evo-task",
            compute_resource="medium",
            is_create_dir=False,
            enable_auto_select=enable_auto_select,
        ),
    )


async def _find_spa_taxids(spa_names: str, timeout: float) -> List[str]:
    """Return taxonomy ids for a target species name."""
    url = DEEP_GENOME_CONFIG.SPA_FAQ_URL.format(
        repo_id=DEEP_GENOME_CONFIG.SPA_REPO_ID
    )
    headers = {
        "X-Auth-Token": await get_token(),
        "Content-Type": "application/json",
    }
    request_params: dict[str, str | int] = {
        "question": spa_names,
        "page_size": 10,
        "page_num": 1,
    }
    disabled_proxies: Any = {"http": None, "https": None}
    response = requests.get(
        url,
        headers=headers,
        params=request_params,
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
    run_identity = RunIdentity.create(
        user_id=user_id,
        scope="evolution_agents_task",
    )
    return create_output_dir(
        user_id=run_identity.user_id,
        task="evolution_agents_task",
        access_key_id=kwargs.get("access_key_id", DEFAULT_ACCESS_KEY_ID),
        secret_access_key=kwargs.get(
            "secret_access_key", DEFAULT_SECRET_ACCESS_KEY
        ),
        obs_server=kwargs.get("obs_server", DEEP_GENOME_CONFIG.OBS_SERVER),
        bucket_name=kwargs.get("bucket_name", DEEP_GENOME_CONFIG.BUCKET_NAME),
        run_identity=run_identity,
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
