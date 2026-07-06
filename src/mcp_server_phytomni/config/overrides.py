# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for building config copies from wrapper arguments.

FieldMaps: RETRIEVAL_CONFIG_FIELD_MAP, CHAT_COMPLETION_CONFIG_FIELD_MAP,
    OBS_TRANSFER_CONFIG_FIELD_MAP, RETRY_CONFIG_FIELD_MAP,
    NL2SQL_CONFIG_FIELD_MAP.
Functions: collect_mapped_overrides, copy_config_with_overrides,
    copy_sensitive_config_with_overrides.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, SecretStr

FieldMap = Mapping[str, str]

RETRIEVAL_CONFIG_FIELD_MAP = {
    "retrieve_url": "RETRIEVE_URL",
    "repo_id_dict": "REPO_ID_DICT",
    "page_num": "PAGE_NUM",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "extra_repo_ids": "EXTRA_REPO_IDS",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    "top_n": "TOP_N",
}

CHAT_COMPLETION_CONFIG_FIELD_MAP = {
    "prompt_file": "PROMPT_FILE",
    "prompt_path": "PROMPT_PATH",
    "frequency_penalty": "FREQUENCY_PENALTY",
    "max_tokens": "MAX_TOKENS",
    "n": "N",
    "presence_penalty": "PRESENCE_PENALTY",
    "reasoning_effort": "REASONING_EFFORT",
    "response_format": "RESPONSE_FORMAT",
    "stream": "STREAM",
    "temperature": "TEMPERATURE",
    "top_p": "TOP_P",
    "user": "USER",
}

OBS_TRANSFER_CONFIG_FIELD_MAP = {
    "server_dir": "TEMP_DIR",
    "obs_server": "OBS_SERVER",
    "bucket_name": "BUCKET_NAME",
    "part_size": "PART_SIZE",
    "task_num": "TASK_NUM",
    "max_concurrency": "MAX_CONCURRENCY",
    "max_workers": "MAX_WORKERS",
}

RETRY_CONFIG_FIELD_MAP = {
    "timeout": "TIMEOUT",
    "retriable_codes": "RETRIABLE_CODES",
    "max_retries": "MAX_RETRIES",
}

NL2SQL_CONFIG_FIELD_MAP = {
    "database_url": "DATABASE_URL",
    "workspace_id": "WORKSPACE_ID",
    "subject_id": "SUBJECT_ID",
    "dialog_id": "DIALOG_ID",
    "need_insight": "NEED_INSIGHT",
    "simplify_response": "SIMPLIFY_RESPONSE",
}


def collect_mapped_overrides(
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    skip_none: bool = True,
) -> dict[str, Any]:
    """Collect non-secret config updates from wrapper argument names.

    Args:
        values: Source mapping of argument names to values.
        field_map: Mapping from source argument names to target config
            field names.
        skip_none: If True, skip None values (default True).

    Returns:
        dict[str, Any]: Dict mapping target field names to values.
    """
    updates: dict[str, Any] = {}
    for source_key, target_key in field_map.items():
        if source_key not in values:
            continue
        value = values[source_key]
        if skip_none and value is None:
            continue
        updates[target_key] = value
    return updates


def copy_config_with_overrides[ConfigT: BaseModel](
    base_config: ConfigT,
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    fixed_updates: Mapping[str, Any] | None = None,
    skip_none: bool = True,
) -> ConfigT:
    """Return a config copy with mapped public wrapper overrides.

    Args:
        base_config: Pydantic config instance to copy.
        values: Source mapping of argument names to values.
        field_map: Mapping from source argument names to target config
            field names.
        fixed_updates: Optional dict of fixed updates to apply before
            field mapping.
        skip_none: If True, skip None values (default True).

    Returns:
        ConfigT: Updated copy of base_config with overrides applied.
    """
    updates = _collect_fixed_updates(fixed_updates, skip_none=skip_none)
    updates.update(
        collect_mapped_overrides(
            values,
            field_map,
            skip_none=skip_none,
        )
    )
    return base_config.model_copy(update=updates)


def copy_sensitive_config_with_overrides[ConfigT: BaseModel](
    base_config: ConfigT,
    values: Mapping[str, Any],
    *,
    field_map: FieldMap | None = None,
    secret_field_map: FieldMap | None = None,
    fixed_updates: Mapping[str, Any] | None = None,
) -> ConfigT:
    """Return a sensitive config copy with secret fields kept separate.

    Args:
        base_config: Pydantic config instance to copy.
        values: Source mapping of argument names to values.
        field_map: Optional mapping from source to non-secret target fields.
        secret_field_map: Optional mapping from source to secret target fields.
        fixed_updates: Optional dict of fixed updates to apply.

    Returns:
        ConfigT: Updated copy of base_config with overrides applied.
    """
    updates = _collect_fixed_updates(fixed_updates, skip_none=True)
    updates.update(
        collect_mapped_overrides(
            values,
            field_map or {},
        )
    )
    updates.update(
        _collect_secret_overrides(
            values,
            secret_field_map or {},
            skip_none=True,
        )
    )
    return base_config.model_copy(update=updates)


def _collect_fixed_updates(
    fixed_updates: Mapping[str, Any] | None,
    *,
    skip_none: bool,
) -> dict[str, Any]:
    if fixed_updates is None:
        return {}
    return {
        key: value
        for key, value in fixed_updates.items()
        if not (skip_none and value is None)
    }


def _collect_secret_overrides(
    values: Mapping[str, Any],
    field_map: FieldMap,
    *,
    skip_none: bool,
) -> dict[str, SecretStr]:
    updates: dict[str, SecretStr] = {}
    for source_key, target_key in field_map.items():
        if source_key not in values:
            continue
        value = values[source_key]
        if skip_none and value is None:
            continue
        updates[target_key] = _to_secret_str(value)
    return updates


def _to_secret_str(value: Any) -> SecretStr:
    if isinstance(value, SecretStr):
        return value
    return SecretStr(str(value))
