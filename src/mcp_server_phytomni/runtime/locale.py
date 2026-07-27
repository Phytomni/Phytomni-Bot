# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Resolve and carry the effective natural-language locale."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final, Literal

__all__ = [
    "SUPPORTED_LOCALES",
    "SupportedLocale",
    "UnsupportedLocaleError",
    "bind_effective_locale",
    "current_effective_locale",
    "infer_query_locale",
    "locale_instruction",
    "message_for",
    "normalize_accept_language",
    "resolve_effective_locale",
]

type SupportedLocale = Literal["en-US", "zh-CN"]
SUPPORTED_LOCALES: Final[frozenset[SupportedLocale]] = frozenset(
    {"en-US", "zh-CN"}
)


class UnsupportedLocaleError(ValueError):
    """Raised when an explicit locale is outside the public enum."""


def normalize_accept_language(value: str | None) -> SupportedLocale | None:
    """Return the first supported language item from an HTTP header.

    Unsupported language items are skipped. The header is only a fallback,
    so a header containing no supported item returns ``None``.
    """
    if not value:
        return None
    for item in value.split(","):
        language = item.split(";", 1)[0].strip().lower()
        if language == "en" or language.startswith("en-"):
            return "en-US"
        if language == "zh" or language.startswith("zh-"):
            return "zh-CN"
    return None


def infer_query_locale(latest_user_query: str) -> SupportedLocale:
    """Infer Chinese only when the latest query contains Han characters."""
    return (
        "zh-CN"
        if any("\u4e00" <= char <= "\u9fff" for char in latest_user_query)
        else "en-US"
    )


def resolve_effective_locale(
    *,
    explicit: str | None,
    accept_language: str | None,
    latest_user_query: str,
) -> SupportedLocale:
    """Resolve locale using body, header, then query precedence."""
    if explicit is not None:
        if explicit not in SUPPORTED_LOCALES:
            raise UnsupportedLocaleError(explicit)
        return explicit
    header_locale = normalize_accept_language(accept_language)
    return header_locale or infer_query_locale(latest_user_query)


def locale_instruction(locale: SupportedLocale) -> str:
    """Return a stable prompt instruction for natural-language output."""
    if locale == "zh-CN":
        return (
            "Write all natural-language prose in Simplified Chinese. "
            "Preserve identifiers, sequences, numbers, citations, tool "
            "names, and structured keys exactly."
        )
    return (
        "Write all natural-language prose in English. Preserve identifiers, "
        "sequences, numbers, citations, tool names, and structured keys "
        "exactly."
    )


_MESSAGES: Final[dict[str, dict[SupportedLocale, str]]] = {
    "unsupported_locale": {
        "en-US": "The requested locale is not supported.",
        "zh-CN": "不支持请求的语言区域。",
    },
    "invalid_request": {
        "en-US": "The request is invalid.",
        "zh-CN": "请求无效。",
    },
    "invalid_argument": {
        "en-US": "invalid request",
        "zh-CN": "请求无效。",
    },
    "unauthenticated": {
        "en-US": "authentication required",
        "zh-CN": "需要身份验证。",
    },
    "forbidden": {
        "en-US": "request is not permitted",
        "zh-CN": "不允许执行此请求。",
    },
    "not_found": {
        "en-US": "resource not found",
        "zh-CN": "未找到资源。",
    },
    "run_state_conflict": {
        "en-US": "request conflicts with current state",
        "zh-CN": "请求与当前状态冲突。",
    },
    "payload_too_large": {
        "en-US": "request payload is too large",
        "zh-CN": "请求内容过大。",
    },
    "rate_limited": {
        "en-US": "request rate limit exceeded",
        "zh-CN": "请求频率超过限制。",
    },
    "internal_invariant_failed": {
        "en-US": "internal server error",
        "zh-CN": "服务器内部错误。",
    },
    "upstream_failed": {
        "en-US": "upstream service failed",
        "zh-CN": "上游服务失败。",
    },
    "unavailable": {
        "en-US": "service unavailable",
        "zh-CN": "服务不可用。",
    },
    "upstream_timeout": {
        "en-US": "upstream service timed out",
        "zh-CN": "上游服务超时。",
    },
    "run_persistence_failed": {
        "en-US": "The completed run could not be persisted.",
        "zh-CN": "无法持久化已完成的运行。",
    },
    "running_without_work": {
        "en-US": "The run has no executable work.",
        "zh-CN": "运行没有可执行的工作。",
    },
    "succeeded_without_persistence": {
        "en-US": "The completed run could not be persisted.",
        "zh-CN": "已完成的运行无法持久化。",
    },
    "input_required_without_surface": {
        "en-US": "The input-required response is invalid.",
        "zh-CN": "需要输入的响应无效。",
    },
    "projection_failed": {
        "en-US": "Result projection failed.",
        "zh-CN": "结果投影失败。",
    },
    "a2ui_action_conflict": {
        "en-US": "This input request has already been handled.",
        "zh-CN": "此输入请求已被处理。",
    },
    "checkpoint_not_available": {
        "en-US": "This input request is no longer available.",
        "zh-CN": "此输入请求已不可用。",
    },
    "routing_contract_violation": {
        "en-US": "The routing contract is invalid.",
        "zh-CN": "路由契约无效。",
    },
    "routing_upstream_failed": {
        "en-US": "The routing upstream service failed.",
        "zh-CN": "路由上游服务失败。",
    },
    "selected_agent_invalid_argument": {
        "en-US": "The selected agent arguments are invalid.",
        "zh-CN": "所选智能体参数无效。",
    },
    "attachment_not_found": {
        "en-US": "The requested attachment was not found.",
        "zh-CN": "未找到请求的附件。",
    },
    "attachment_not_supported": {
        "en-US": "This agent does not support attachments.",
        "zh-CN": "此智能体不支持附件。",
    },
    "attachment_format_unsupported": {
        "en-US": "The attachment format is not supported.",
        "zh-CN": "不支持该附件格式。",
    },
    "attachment_duplicate": {
        "en-US": "The same attachment path was supplied more than once.",
        "zh-CN": "同一附件路径被重复提交。",
    },
    "attachment_limit_exceeded": {
        "en-US": "The attachment limits were exceeded.",
        "zh-CN": "附件数量或大小超过限制。",
    },
    "attachment_purpose_mismatch": {
        "en-US": "The attachment purpose does not match this request.",
        "zh-CN": "附件用途与本次请求不匹配。",
    },
    "attachment_description_required": {
        "en-US": "A dataset description is required.",
        "zh-CN": "必须提供数据集描述。",
    },
}


def message_for(code: str, locale: SupportedLocale) -> str:
    """Return a fixed, request-data-free message for ``code`` and locale."""
    try:
        return _MESSAGES[code][locale]
    except KeyError as exc:
        raise KeyError(f"unknown localized message code: {code}") from exc


_EFFECTIVE_LOCALE: ContextVar[SupportedLocale] = ContextVar(
    "phytomni_effective_locale", default="en-US"
)


def bind_effective_locale(locale: SupportedLocale) -> Token[SupportedLocale]:
    """Bind one locale and return the token required to restore it."""
    if locale not in SUPPORTED_LOCALES:
        raise UnsupportedLocaleError(locale)
    return _EFFECTIVE_LOCALE.set(locale)


def current_effective_locale() -> SupportedLocale:
    """Return the locale bound to the current request context."""
    return _EFFECTIVE_LOCALE.get()
