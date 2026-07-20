# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Analyst submission wrapper and per-call config copies.

Exports the ``submit`` compatibility wrapper and the analyst-specific
override builders. The input-fingerprint and reuse-decision helpers now
live in ``runtime/task_dedup.py`` so both analyst entry points and the
dispatch seam can share them. ``AnalystAgent`` comes from ``.core`` to
keep the import graph acyclic.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import get_sensitive_config
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...storage.path_policy import RunIdentity
from .core import (
    AnalystAgent,
    SubmissionOptions,
    resolve_submission_options,
)
from .defaults import (
    ANALYST_CONFIG,
    ANALYST_CONFIG_FIELD_MAP,
    ANALYST_SECRET_FIELD_MAP,
    ANALYST_SENSITIVE_FIELD_MAP,
)

# ``_analyst_config_with_overrides`` consumes these as explicit parameters;
# strip them from the splatted kwargs or Python raises "got multiple values".
_CONFIG_EXPLICIT_KEYS = frozenset(
    {"user_id", "is_create_dir", "output_dir", "compute_resource"}
)


def _shared_arun_kwargs(
    goal_description: str,
    output_dir: str,
    compute_resource: str,
    data_list: Any,
) -> dict[str, Any]:
    """Return the shared AnalystAgent.arun keyword projection."""
    return SubmissionOptions.build_arun_kwargs(
        goal_description,
        output_dir=output_dir,
        compute_resource=compute_resource,
        data_list=data_list,
    )


def _analyst_config_with_overrides(
    options: SubmissionOptions,
    **kwargs: Any,
):
    """Build an AnalystConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        ANALYST_CONFIG,
        kwargs,
        ANALYST_CONFIG_FIELD_MAP,
        fixed_updates=options.config_updates(),
    )


def _sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
    return copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=ANALYST_SENSITIVE_FIELD_MAP,
        secret_field_map=ANALYST_SECRET_FIELD_MAP,
    )


def _submit_user_and_thread_id(
    user_id: Any,
    scope: str,
    operation: str,
) -> tuple[str, str]:
    """Return compatible user IDs and run-scoped fallback thread IDs."""
    if user_id:
        resolved_user_id = str(user_id)
        return resolved_user_id, resolved_user_id

    run_identity = RunIdentity.create(user_id=None, scope=scope)
    return run_identity.user_id, run_identity.scoped_id("thread", operation)


def _build_submit_agent(
    kwargs: dict[str, Any],
    scope: str,
    operation: str,
    cache_label: str,
) -> tuple[Any, str, str, str]:
    """Resolve wrapper kwargs into a cached collision-safe AnalystAgent."""
    options = resolve_submission_options(ANALYST_CONFIG, kwargs)
    user_id, thread_id = _submit_user_and_thread_id(
        options.user_id,
        scope,
        operation,
    )
    analyst_config = _analyst_config_with_overrides(
        replace(options, user_id=user_id),
        **{k: v for k, v in kwargs.items() if k not in _CONFIG_EXPLICIT_KEYS},
    )
    sensitive_config = _sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        cache_label,
        lambda: AnalystAgent(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            analyst_config=analyst_config,
            sensitive_config=sensitive_config,
        ),
    )
    return agent, options.output_dir, options.compute_resource, thread_id


async def submit(
    goal_description: str,
    data_list: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Submit a prepared analysis plan through AnalystAgent.

    Args:
        goal_description: Research goal or analysis objective.
        data_list: Data files and descriptions passed directly to the agent.
        **kwargs: Optional config, credential, output, compute-resource,
            metadata, user, thread, retry, and auto-selection overrides.

    Returns:
        AnalystAgent result payload with task id, output directory, job name,
        and compute resource.
    """
    meta = kwargs.get("meta", "")
    enable_auto_select = kwargs.get("enable_auto_select", True)
    meta_meta = kwargs.get("meta_meta")
    agent, output_dir, compute_resource, thread_id = _build_submit_agent(
        kwargs,
        "analyst-submit",
        "submit",
        "AnalystAgent.submit",
    )
    return await agent.arun(
        **_shared_arun_kwargs(
            goal_description,
            output_dir,
            compute_resource,
            data_list,
        ),
        preset_plan=meta + (meta_meta or ""),
        thread_id=thread_id,
        is_auto_select=enable_auto_select,
        is_polling=False,
    )
