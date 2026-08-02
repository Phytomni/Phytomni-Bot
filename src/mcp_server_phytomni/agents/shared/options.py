# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Shared option builders for public agent wrappers.

Exports submit option specs, resource-copy helpers, retry-code resolution, and
keyword builders used by Analyst-backed domain wrappers before they call chat
and task-submission services.
"""

from dataclasses import dataclass
from typing import Any

from ...runtime.locale import (
    SupportedLocale,
    current_effective_locale,
    locale_instruction,
)

__all__ = [
    "SubmitKwargsSpec",
    "build_chat_kwargs",
    "build_resolver_chat_kwargs",
    "build_submit_kwargs",
    "copy_resource_dict",
    "resolve_agent_locale",
    "retry_codes_from_kwargs",
]


@dataclass(frozen=True)
class SubmitKwargsSpec:
    """Static submit options for one Analyst-backed wrapper.

    Attributes:
        task_name: Analysis platform task name.
        compute_resource: Default compute resource label.
        execute_code: Whether submitted tasks should execute code.
        is_create_dir: Optional directory-creation override.
        enable_auto_select: Optional auto-selection override.
    """

    task_name: str
    compute_resource: str
    execute_code: bool = True
    is_create_dir: bool | None = None
    enable_auto_select: bool | None = None


def copy_resource_dict(value: Any, default: dict) -> dict:
    """Return a copied nested resource dictionary.

    Args:
        value: Optional override dictionary to copy.
        default: Default dictionary copied when ``value`` is None.

    Returns:
        A shallow copy of the outer mapping and each nested resource mapping.
    """
    source = default if value is None else value
    return {key: dict(item) for key, item in source.items()}


def retry_codes_from_kwargs(kwargs: dict[str, Any], config: Any) -> list[int]:
    """Return retriable status codes from overrides or config defaults.

    Args:
        kwargs: Public wrapper keyword arguments.
        config: Config object that provides ``RETRIABLE_CODES``.

    Returns:
        List of HTTP status codes that should trigger retry logic.
    """
    retriable_codes = kwargs.get("retriable_codes")
    if retriable_codes is None:
        return list(config.RETRIABLE_CODES)
    return list(retriable_codes)


def resolve_agent_locale(
    locale: SupportedLocale | None,
) -> SupportedLocale:
    """Resolve an agent call's explicit or request-bound locale."""
    return locale or current_effective_locale()


def build_chat_kwargs(
    kwargs: dict[str, Any],
    config: Any,
    sensitive_config: Any,
    *,
    locale: SupportedLocale | None = None,
) -> dict[str, Any]:
    """Return common phyto_chat keyword arguments.

    Args:
        kwargs: Public wrapper keyword arguments. The optional
            ``with_follow_up`` key opts the chat subgraph router into
            its follow-up question branch (``follow_up_node``) after
            ``generate_node``. Only included in the returned dict when
            the caller explicitly passes it; absent keys leave the
            router's existing ``True``-default behavior untouched for
            consumer-agent sites that have not migrated to the
            explicit pattern.
        config: Public config object with chat and retry defaults.
        sensitive_config: Sensitive config object with model credentials.

    Returns:
        Keyword arguments suitable for forwarding to ``phyto_chat``.
    """
    effective_locale = resolve_agent_locale(locale or kwargs.get("locale"))
    result: dict[str, Any] = {
        "prompt_file": kwargs.get("prompt_file", config.PROMPT_FILE),
        "prompt_path": kwargs.get("prompt_path", config.PROMPT_PATH),
        "api_key": kwargs.get(
            "api_key", sensitive_config.API_KEY.get_secret_value()
        ),
        "base_url": kwargs.get("base_url", sensitive_config.BASE_URL),
        "model": kwargs.get("model", sensitive_config.MODEL_ID),
        "frequency_penalty": kwargs.get(
            "frequency_penalty", config.FREQUENCY_PENALTY
        ),
        "n": kwargs.get("n", config.N),
        "presence_penalty": kwargs.get(
            "presence_penalty", config.PRESENCE_PENALTY
        ),
        "reasoning_effort": kwargs.get(
            "reasoning_effort", config.REASONING_EFFORT
        ),
        "response_format": kwargs.get(
            "response_format", config.RESPONSE_FORMAT
        ),
        "stream": kwargs.get("stream", config.STREAM),
        "temperature": kwargs.get("temperature", config.TEMPERATURE),
        "top_p": kwargs.get("top_p", config.TOP_P),
        "user": kwargs.get("user", config.USER),
        "timeout": kwargs.get("timeout", config.TIMEOUT),
        "retriable_codes": retry_codes_from_kwargs(kwargs, config),
        "max_retries": kwargs.get("max_retries", config.MAX_RETRIES),
        "locale": effective_locale,
        "locale_instruction": locale_instruction(effective_locale),
    }
    relay_timeout_profile = getattr(config, "RELAY_TIMEOUT_PROFILE", None)
    if relay_timeout_profile is not None:
        result["relay_timeout_profile"] = relay_timeout_profile
    if "with_follow_up" in kwargs:
        result["with_follow_up"] = kwargs["with_follow_up"]
    return result


def build_resolver_chat_kwargs(
    prompt_path: str,
    response_format: dict[str, Any],
    config: Any,
    sensitive_config: Any,
    *,
    locale: SupportedLocale | None = None,
) -> dict[str, Any]:
    """Return shared chat options for a structured query resolver.

    Resolver callers use a fixed system prompt and provider response schema;
    keeping those two overrides together prevents domain resolvers from
    duplicating the same option-building sequence.

    Args:
        prompt_path: System prompt path used by the resolver.
        response_format: Structured-output schema accepted by ``phyto_chat``.
        config: Domain config object with chat defaults.
        sensitive_config: Sensitive config object with model credentials.

    Returns:
        Keyword arguments suitable for forwarding to ``phyto_chat``.
    """
    result = build_chat_kwargs(
        {"prompt_path": prompt_path},
        config,
        sensitive_config,
        locale=locale,
    )
    result["response_format"] = response_format
    return result


def build_submit_kwargs(
    kwargs: dict[str, Any],
    config: Any,
    sensitive_config: Any,
    credentials: tuple[str, str],
    spec: SubmitKwargsSpec,
) -> dict[str, Any]:
    """Return common Analyst submit keyword arguments.

    Args:
        kwargs: Public wrapper keyword arguments.
        config: Public config object with OBS, retry, and resource defaults.
        sensitive_config: Sensitive config object with model credentials.
        credentials: Default OBS access key id and secret access key.
        spec: Static task options for the wrapper.

    Returns:
        Keyword arguments suitable for Analyst task submission wrappers.
    """
    access_key_id, secret_access_key = credentials
    result = {
        "execute_code": spec.execute_code,
        "model_url": kwargs.get("model_url", sensitive_config.CODER_URL),
        "model_name": kwargs.get("model_name", sensitive_config.CODER_MODEL),
        "coder_api_key": kwargs.get(
            "coder_api_key",
            sensitive_config.CODER_API_KEY.get_secret_value(),
        ),
        "access_key_id": kwargs.get("access_key_id", access_key_id),
        "secret_access_key": kwargs.get(
            "secret_access_key", secret_access_key
        ),
        "obs_server": kwargs.get("obs_server", config.OBS_SERVER),
        "bucket_name": kwargs.get("bucket_name", config.BUCKET_NAME),
        "analysis_url": kwargs.get("analysis_url", config.ANALYSIS_URL),
        "region": kwargs.get("region", config.ANALYSIS_REGION),
        "task_name": spec.task_name,
        "resource_dict": copy_resource_dict(
            kwargs.get("resource_dict"), config.RESOURCE
        ),
        "app_id_dict": dict(kwargs.get("app_id_dict") or config.APP_ID),
        "compute_resource": spec.compute_resource,
        "timeout": kwargs.get("timeout", config.TIMEOUT),
        "retriable_codes": retry_codes_from_kwargs(kwargs, config),
        "max_retries": kwargs.get("max_retries", config.MAX_RETRIES),
        "max_poll": kwargs.get("max_poll", config.MAX_POLL),
    }
    if spec.is_create_dir is not None:
        result["is_create_dir"] = spec.is_create_dir
    if spec.enable_auto_select is not None:
        result["enable_auto_select"] = spec.enable_auto_select
    return result
