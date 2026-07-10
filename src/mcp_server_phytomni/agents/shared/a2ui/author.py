# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Surface Author: domain templates → LLM props → thin fallback."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from importlib import import_module
from typing import Any, Literal, TypedDict

from mcp.shared.exceptions import McpError
from openai import APIError, OpenAIError
from pydantic import ValidationError

from ....common.responses import message_content
from .build import build_a2ui_value, mint_surface_id
from .domain_templates import match_domain_template
from .rules import select_chat_a2ui_widget
from .schemas import (
    REVIEW_BODY_MAX_CHARS,
    REVIEW_CONFIRM_TITLE,
    A2uiWidget,
    ChoiceProps,
    ConfirmProps,
    FormProps,
)
from .templates import (
    build_choice_template_props,
    build_form_template_props,
)

_LOGGER = logging.getLogger(__name__)

A2uiProps = ConfirmProps | FormProps | ChoiceProps

LlmPropsFn = Callable[
    ["AuthorContext", A2uiWidget],
    Awaitable[dict[str, Any] | None],
]


class AuthorContext(TypedDict):
    """Input context for authoring an A2UI downlink surface."""

    text: str
    agent: Literal["chat", "review"]


def _select_widget(ctx: AuthorContext) -> A2uiWidget:
    """Pick the widget; default confirm for both agents defensively."""
    return select_chat_a2ui_widget(ctx["text"]) or "confirm"


def _thin_props(
    widget: A2uiWidget,
    text: str,
    *,
    agent: Literal["chat", "review"] = "chat",
) -> A2uiProps:
    """Return thin template / confirm body fallback props."""
    if widget == "form":
        return build_form_template_props()
    if widget == "choice":
        return build_choice_template_props()
    # Review confirm title lives in schemas (shared with review.py).
    title = REVIEW_CONFIRM_TITLE if agent == "review" else "Confirm"
    body_max = REVIEW_BODY_MAX_CHARS if agent == "review" else 500
    return ConfirmProps(title=title, body=text[:body_max])


def _validate_props(
    widget: A2uiWidget,
    raw: dict[str, Any],
) -> A2uiProps | None:
    """Validate LLM/raw dict into typed props, or None on failure."""
    try:
        if widget == "form":
            return FormProps.model_validate(raw)
        if widget == "choice":
            return ChoiceProps.model_validate(raw)
        return ConfirmProps.model_validate(raw)
    except ValidationError:
        return None


def _domain_props_if_compatible(
    text: str,
    widget: A2uiWidget,
) -> A2uiProps | None:
    """Return domain props when the match widget equals ``widget``."""
    matched = match_domain_template(text)
    if matched is not None and matched.widget == widget:
        return matched.props
    return None


def _build_surface(widget: A2uiWidget, props: A2uiProps) -> dict[str, Any]:
    """Mint a surface id and build the downlink value."""
    return build_a2ui_value(
        surface_id=mint_surface_id(),
        widget=widget,
        props=props,
    )


def author_a2ui_surface_offline(ctx: AuthorContext) -> dict[str, Any]:
    """Author a downlink sync: domain template → thin (no LLM)."""
    text = ctx["text"]
    widget = _select_widget(ctx)
    props = _domain_props_if_compatible(text, widget)
    if props is None:
        props = _thin_props(widget, text, agent=ctx["agent"])
    return _build_surface(widget, props)


async def _default_llm_props(
    ctx: AuthorContext,
    widget: A2uiWidget,
) -> dict[str, Any] | None:
    """Best-effort JSON props via phyto_chat; return None on failure."""
    # Lazy: keep package import free of agents.chat.service.
    phyto_chat = import_module(
        "mcp_server_phytomni.agents.chat.service"
    ).phyto_chat

    prompt = (
        "Return a JSON object of A2UI widget props for the "
        f"{widget!r} surface. Text:\n{ctx['text'][:500]}"
    )
    try:
        response = await phyto_chat(
            prompt,
            response_format={"type": "json_object"},
            with_follow_up=False,
        )
        content = message_content(response)
        start = content.find("{")
        end = content.rfind("}") + 1
        if start == -1 or end <= start:
            return None
        parsed = json.loads(content[start:end])
        return parsed if isinstance(parsed, dict) else None
    except (
        McpError,
        OpenAIError,
        APIError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
        OSError,
        ConnectionError,
        TimeoutError,
        json.JSONDecodeError,
    ):
        _LOGGER.warning(
            "A2UI LLM props author failed; falling back to thin",
            exc_info=True,
        )
        return None


async def author_a2ui_surface(
    ctx: AuthorContext,
    *,
    llm_props_fn: LlmPropsFn | None = None,
) -> dict[str, Any]:
    """Return a phyto.a2ui downlink dict (build_a2ui_value output).

    Pipeline: widget select → domain template (widget-compatible) →
    LLM props (injectable or default) → thin fallback.
    """
    text = ctx["text"]
    widget = _select_widget(ctx)
    props = _domain_props_if_compatible(text, widget)
    if props is None:
        fn = llm_props_fn if llm_props_fn is not None else _default_llm_props
        raw = await fn(ctx, widget)
        if raw is not None:
            props = _validate_props(widget, raw)
        if props is None:
            props = _thin_props(widget, text, agent=ctx["agent"])
    return _build_surface(widget, props)
