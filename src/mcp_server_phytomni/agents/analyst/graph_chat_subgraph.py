# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Prep + post chat-subgraph nodes for AnalystAgent.

Exports :class:`AnalystChatSubgraphMixin`, the prep + post halves of
each chat node (``parse_query`` / ``data_select`` / ``plan`` /
``check`` / ``tool_extract``). The legacy single-node bodies stay in
``graph.py``; this mixin only owns the prep + post pairs surrounding
the shared chat node mounted into the compiled graph.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.prompts import get_prompt
from ...common.responses import message_content, parse_json_object_fragment
from ...config.data_loaders import load_species_data
from ...graphs.chat_adapters import (
    build_chat_input,
    build_chat_kwargs_for,
)

if TYPE_CHECKING:
    from .agent import AnalystAgentsState
else:
    AnalystAgentsState = dict[str, Any]

logger = logging.getLogger(__name__)

# Data selection returns a compact JSON mapping; keep enough context budget
# for the comparatively large pre-prepared species catalog.
_DATA_SELECTION_MAX_TOKENS = 4096


def _parse_json_object_preserving_errors(text: str) -> Any:
    """Use the common object parser while retaining legacy JSON errors."""
    parsed = parse_json_object_fragment(text)
    if parsed:
        return parsed
    if text:
        return json.loads(text)
    return {}


class AnalystChatSubgraphMixin:
    """Prep + post halves for the five analyst chat sites."""

    async def parse_query_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the chat payload for the parse-query call.

        Mirrors the prompt-building half of ``parse_query_node``.
        When ``state["goal_description"]`` is already set, emits the
        legacy early-return delta with ``chat_payload=None`` so the
        conditional prep-router skips the chat node and the post node
        becomes a no-op; otherwise builds the split-query prompt and
        emits a ``ChatInput`` payload plus the ``pending_post`` sentinel
        the after-chat router reads to branch back to
        ``parse_query_post_node``.

        Args:
            state: The current workflow state. Reads ``goal_description``,
                ``data_list``, ``plan``, and ``query``.

        Returns:
            Either the legacy early-return delta (with ``chat_payload``
            cleared and ``pending_post`` staged) or the chat-call
            payload with the ``pending_post`` sentinel.
        """
        if state["goal_description"]:
            return {
                "goal_description": state["goal_description"],
                "data_list": state["data_list"],
                "plan": state.get("plan", None),
                "chat_payload": None,
                "pending_post": "parse_query_post_node",
            }
        parse_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
            "user/split_query",
            {"user_query": state["query"]},
        )
        chat_kwargs = build_chat_kwargs_for(
            self.analyst_config,
            self.sensitive_config,
            response_format={"type": "json_schema"},
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(parse_prompt, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "parse_query_post_node",
        }

    async def parse_query_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the parse-query chat response into the legacy delta.

        Mirrors the response-parsing half of ``parse_query_node`` but
        reads the chat response from ``state['chat_response']``
        instead of awaiting a fresh ``phyto_chat`` call. When the prep
        node took the early-return branch (``chat_payload is None``),
        the prep already committed the legacy delta to state, so this
        node returns an empty dict.

        Args:
            state: The current workflow state. Reads ``chat_payload``
                (skip sentinel) and ``chat_response`` written by the
                shared chat node.

        Returns:
            The legacy ``parse_query_node`` delta (``goal_description``
            / ``data_list`` / ``plan``), or ``{}`` when the prep node
            handled the early-return path.
        """
        if state.get("chat_payload") is None:
            return {}
        content = message_content(state.get("chat_response") or "") or "{}"
        result = _parse_json_object_preserving_errors(content)
        return {
            "goal_description": (
                result["goal_description"]
                if result["goal_description"]
                else None
            ),
            "data_list": (
                json.loads(result["data_list"])
                if result["data_list"]
                else None
            ),
            "plan": result["plan"] if result["plan"] else "",
        }

    async def data_select_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the chat payload for the data-selection call.

        Mirrors the prompt-building half of ``data_select_node``.
        Loads pre-prepared species data and builds the selection
        prompt; raises ``McpError`` on a species-data load failure
        (matching the legacy node), then emits the chat payload.

        Args:
            state: The current workflow state. Reads
                ``goal_description`` and ``data_list``.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload`` and ``"data_select_post_node"`` under
            ``pending_post``.

        Raises:
            McpError: If loading species data fails.
        """
        try:
            species_data = load_species_data(
                self.analyst_config.PRE_PREPARED_DATA_PATH
            )
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        "Failed to load species data list from "
                        f"{self.analyst_config.PRE_PREPARED_DATA_PATH}"
                    ),
                )
            ) from exc
        data_list = state["data_list"]
        user_data_summary = json.dumps(data_list)
        selection_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
            "user/data_selection",
            {
                "goal_description": state["goal_description"],
                "user_data_list": user_data_summary,
                "available_data_list": json.dumps(species_data),
            },
        )
        chat_kwargs = build_chat_kwargs_for(
            self.analyst_config,
            self.sensitive_config,
            response_format={"type": "json_schema"},
            locale=state.get("locale"),
        )
        chat_kwargs["max_tokens"] = _DATA_SELECTION_MAX_TOKENS
        chat_payload = build_chat_input(selection_prompt, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "data_select_post_node",
        }

    async def data_select_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the data-selection chat response into the legacy delta.

        Mirrors the response-parsing half of ``data_select_node`` but
        reads the chat response from ``state['chat_response']``
        instead of awaiting a fresh ``phyto_chat`` call. Preserves the
        legacy ``McpError`` raise on JSON parse failures.

        Args:
            state: The current workflow state. Reads ``data_list`` and
                the upstream ``chat_response`` written by the shared
                chat node.

        Returns:
            A state delta with the updated ``data_list`` that merges
            the LLM-selected files into the user-supplied list.

        Raises:
            McpError: If parsing the LLM response fails.
        """
        selection_response = state.get("chat_response") or ""
        data_list = state["data_list"]
        selected_data: dict[Any, Any] = {}
        content = message_content(selection_response)
        if content:
            try:
                parsed_response = _parse_json_object_preserving_errors(content)
                if "selected_data" in parsed_response:
                    selected_data = parsed_response["selected_data"]
                else:
                    selected_data = parsed_response

            except (json.JSONDecodeError, ValueError) as exc:
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=(
                            "Failed to parse data selection response: "
                            f"{str(exc)}"
                        ),
                    )
                ) from exc

        final_data_list = {**data_list, **selected_data}
        logger.debug("AutoSelect Data: %s", final_data_list)

        return {"data_list": final_data_list}

    async def plan_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the chat payload for the plan-generation call.

        Mirrors the prompt-building half of ``plan_node``, including
        the four-way prompt template branch on the presence of
        ``plan_feedback`` and ``obs_file_list``. Always emits a chat
        payload (no early-return path).

        Args:
            state: The current workflow state. Reads
                ``goal_description``, ``method_context``,
                ``plan_feedback``, ``plan``, and ``obs_file_list``.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload`` and ``"plan_post_node"`` under
            ``pending_post``.
        """
        if state.get("plan_feedback"):
            if state["obs_file_list"]:
                user_query = get_prompt(
                    self.analyst_config.PROMPT_FILE,
                    "user/analysis_retrieve_file_feedback",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "upload_context": state["method_context"][
                            "upload_context"
                        ],
                        "feedback": state["plan_feedback"],
                        "raw_plan": state.get("plan", ""),
                        "user_query": state["goal_description"],
                    },
                )
            else:
                user_query = get_prompt(
                    self.analyst_config.PROMPT_FILE,
                    "user/analysis_retrieve_feedback",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "feedback": state["plan_feedback"],
                        "raw_plan": state.get("plan", ""),
                        "user_query": state["goal_description"],
                    },
                )
        else:
            if state["obs_file_list"]:
                user_query = get_prompt(
                    self.analyst_config.PROMPT_FILE,
                    "user/analysis_retrieve_file",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "upload_context": state["method_context"][
                            "upload_context"
                        ],
                        "user_query": state["goal_description"],
                    },
                )
            else:
                user_query = get_prompt(
                    self.analyst_config.PROMPT_FILE,
                    "user/analysis_retrieve",
                    {
                        "retrieve_results": state["method_context"][
                            "retrieve_context"
                        ],
                        "user_query": state["goal_description"],
                    },
                )
        chat_kwargs = build_chat_kwargs_for(
            self.analyst_config,
            self.sensitive_config,
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(user_query, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "plan_post_node",
        }

    async def plan_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the plan chat response into the legacy delta.

        Mirrors the response-parsing half of ``plan_node`` but reads
        the chat response from ``state['chat_response']`` instead of
        awaiting a fresh ``phyto_chat`` call. Preserves the
        ``McpError`` raise when the LLM returns empty content.

        Args:
            state: The current workflow state. Reads ``plan_retries``
                and the upstream ``chat_response`` written by the
                shared chat node.

        Returns:
            A state delta with the generated ``plan``, the
            incremented ``plan_retries``, and a reset
            ``plan_feedback``.

        Raises:
            McpError: If the LLM returned no usable content.
        """
        content = message_content(state.get("chat_response") or "")
        if not content:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message="Failed to generate plan: "
                    "Invalid response from language model",
                )
            )
        logger.debug("Plan: %s", content)
        return {
            "plan": content,
            "plan_retries": state.get("plan_retries", 0) + 1,
            "plan_feedback": None,
        }

    async def check_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the chat payload for the plan-check call.

        Mirrors the prompt-building half of ``check_node``. When the
        preset-plan-with-no-retrieval shortcut applies, emits the
        legacy auto-approved delta with ``chat_payload=None`` so the
        conditional prep-router skips the chat node and the post node
        becomes a no-op; otherwise builds the critic prompt and emits
        the chat payload.

        Args:
            state: The current workflow state. Reads
                ``is_preset_plan``, ``method_context``,
                ``goal_description``, ``data_list``, and ``plan``.

        Returns:
            Either the legacy auto-approved delta (with
            ``chat_payload`` cleared and ``pending_post`` staged) or
            the chat-call payload with the ``pending_post`` sentinel.
        """
        if state.get("is_preset_plan") and state.get("method_context") is None:
            logger.info("Check (Reset Plan): skipping validation - approved")
            return {
                "plan_feedback": "APPROVED",
                "chat_payload": None,
                "pending_post": "check_post_node",
            }

        check_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
            "user/meta_step_check",
            {
                "goal_description": state["goal_description"],
                "data_list": str(state["data_list"]),
                "method_context": state["method_context"],
                "current_plan": state["plan"],
            },
        )
        chat_kwargs = build_chat_kwargs_for(
            self.analyst_config,
            self.sensitive_config,
            response_format={"type": "json_object"},
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(check_prompt, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "check_post_node",
        }

    async def check_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the plan-check chat response into the legacy delta.

        Mirrors the response-parsing half of ``check_node`` but reads
        the chat response from ``state['chat_response']`` instead of
        awaiting a fresh ``phyto_chat`` call. Preserves the
        retry-exhaustion ``McpError`` raise when the critic score
        stays below ``PLAN_MIN_SCORE`` after retries.

        Args:
            state: The current workflow state. Reads ``plan_retries``
                and the upstream ``chat_response`` written by the
                shared chat node.

        Returns:
            A state delta with the next ``plan_feedback`` value, or
            ``{}`` when the prep node handled the auto-approve path.

        Raises:
            McpError: If the plan critic score stays below
                ``PLAN_MIN_SCORE`` after ``MAX_RETRIES``.
        """
        if state.get("chat_payload") is None:
            return {}
        max_retries = self.analyst_config.MAX_RETRIES
        current_retries = state.get("plan_retries", 0)
        try:
            content = message_content(state.get("chat_response") or "")
            result = _parse_json_object_preserving_errors(content or "{}")
            score = result.get("score", 0)
            decision = result.get("decision", "REJECTED")
            feedback = result.get("feedback", "")
        except (json.JSONDecodeError, TypeError, AttributeError):
            score = 0
            decision = "REJECTED"
            feedback = ""
        logger.info(
            "Check: retries=%s/%s score=%s",
            current_retries,
            max_retries,
            score,
        )
        logger.debug("Check feedback: %s", feedback)
        min_score = self.analyst_config.PLAN_MIN_SCORE
        if decision == "APPROVED" and (min_score == 0 or score >= min_score):
            return {"plan_feedback": "APPROVED"}
        if current_retries >= max_retries:
            if min_score == 0:
                return {"plan_feedback": "APPROVED"}
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=(
                        "Analysis plan rejected: best critic score "
                        f"{score} is below PLAN_MIN_SCORE {min_score} "
                        f"after {max_retries} retries. Latest "
                        f"feedback: {feedback}"
                    ),
                )
            )
        return {"plan_feedback": feedback}

    async def tool_extract_prep_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Stage the chat payload for the tool-extraction call.

        Mirrors the prompt-building half of ``tool_extract_node``.

        Args:
            state: The current workflow state. Reads ``plan``.

        Returns:
            A state delta with the ``ChatInput`` dict under
            ``chat_payload`` and ``"tool_extract_post_node"`` under
            ``pending_post``.
        """
        tool_extract_prompt = get_prompt(
            self.analyst_config.PROMPT_FILE,
            "user/tool_extract",
            {"plan": state["plan"]},
        )
        chat_kwargs = build_chat_kwargs_for(
            self.analyst_config,
            self.sensitive_config,
            response_format={"type": "json_object"},
            locale=state.get("locale"),
        )
        chat_payload = build_chat_input(tool_extract_prompt, chat_kwargs)
        return {
            "chat_payload": chat_payload,
            "pending_post": "tool_extract_post_node",
        }

    async def tool_extract_post_node(
        self: Any, state: AnalystAgentsState
    ) -> dict[str, Any]:
        """Parse the tool-extraction chat response into the legacy delta.

        Mirrors the response-parsing half of ``tool_extract_node`` but
        reads the chat response from ``state['chat_response']``
        instead of awaiting a fresh ``phyto_chat`` call.

        Args:
            state: The current workflow state. Reads the upstream
                ``chat_response`` written by the shared chat node.

        Returns:
            A state delta with the parsed ``extracted_tools`` list.
        """
        content = message_content(state.get("chat_response") or "") or "{}"
        result = _parse_json_object_preserving_errors(content)
        logger.debug("Extracted tools: %s", result["tools"])
        return {"extracted_tools": result["tools"]}
