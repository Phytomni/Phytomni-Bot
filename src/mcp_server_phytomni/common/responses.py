# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Helpers for response parsing and bounded prompt fragments.

Functions: message_content, first_message, parse_json_list_fragment,
    parse_follow_up_questions, attach_message_payload,
    join_limited_fragments.
"""

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, List, Optional

# Matches any `[<content>]` bracket where the content is NOT just
# digits (those are valid `[N]` citations) and the bracket is NOT
# immediately followed by `(` (markdown link / image) or `:`
# (markdown reference-style link footer).
_CITATION_RESIDUE_PATTERN = re.compile(
    r"\[(?![0-9])(?P<content>[^\[\]]{1,80})\](?![(:])"
)


def assert_no_citation_residue(answer: str) -> None:
    """Assert the markdown body carries zero leaked citation markers.

    Cited-agent answers must contain only ``[N]`` numeric citation
    markers and markdown image / link / reference-style syntax in
    bracket form. Any other ``[...]`` shape indicates the LLM
    drifted away from the Align-A contract — most commonly
    ``[document: InterPro]`` style named pseudo-citations or
    ``[document: 32]`` style numeric-with-prefix drift that the
    widened post-processor regex catches but the prompt should
    still forbid.

    Args:
        answer: Raw assistant message content (markdown string).

    Raises:
        AssertionError: When any non-numeric, non-link bracket
            content survives in the markdown body, listing the
            offending samples up to 10.
    """
    leaks = [
        match.group("content").strip()
        for match in _CITATION_RESIDUE_PATTERN.finditer(answer)
    ]
    if not leaks:
        return
    sample = leaks[:10]
    extra = f" (and {len(leaks) - 10} more)" if len(leaks) > 10 else ""
    raise AssertionError(
        f"citation marker leaked into client markdown: {sample}{extra}"
    )


def message_content(response: Any) -> str:
    """Return the first assistant message content from an OpenAI-style dict.

    Args:
        response: OpenAI-style response dict with 'choices' list
            containing messages.

    Returns:
        str: Content of the first assistant message, or empty
            string if not found.
    """
    message = first_message(response)
    return str(message.get("content", "")) if message else ""


def first_message(response: Any) -> Optional[dict[str, Any]]:
    """Return the first OpenAI-style message dictionary if present.

    Args:
        response: OpenAI-style response dict with 'choices' list.

    Returns:
        Optional[dict[str, Any]]: First message dict from choices,
            or None if not present.
    """
    if not isinstance(response, dict):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    message = choice.get("message")
    return message if isinstance(message, dict) else None


def parse_json_list_fragment(text: str) -> List[Any]:
    """Parse a JSON list embedded in model output text.

    Args:
        text: String containing a JSON list possibly embedded in other text.

    Returns:
        List[Any]: Parsed list if valid JSON list found, empty list otherwise.
    """
    if not text:
        return []
    start_index = text.find("[")
    end_index = text.rfind("]") + 1
    if start_index == -1 or end_index <= start_index:
        return []
    try:
        parsed = json.loads(text[start_index:end_index])
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_follow_up_questions(text: str) -> List[str]:
    """Parse follow-up questions from a JSON list embedded in model output.

    Args:
        text: String containing a JSON list of follow-up questions.

    Returns:
        List[str]: List of question strings, or empty list if parsing fails.
    """
    return parse_json_list_fragment(text)


def attach_message_payload(
    phyto_response: dict[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach payload fields to the first assistant message.

    Args:
        phyto_response: OpenAI-style response dict with 'choices' structure.
        payload: Mapping of fields to attach to the first message.

    Returns:
        dict[str, Any]: Modified response with payload fields
            merged into first message.
    """
    if not isinstance(phyto_response, dict) or "choices" not in phyto_response:
        phyto_response = {"choices": [{"message": {}}]}
    if not phyto_response["choices"]:
        phyto_response["choices"].append({"message": {}})
    if "message" not in phyto_response["choices"][0]:
        phyto_response["choices"][0]["message"] = {}

    phyto_response["choices"][0]["message"].update(dict(payload))
    return phyto_response


def join_limited_fragments(
    fragments: Iterable[str],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Join fragments until their combined length reaches the limit.

    Args:
        fragments: Iterable of string fragments to join.
        max_tokens: Maximum combined length (in characters) allowed.
        initial_length: Starting length before processing
            fragments (default 0).

    Returns:
        tuple[str, int]: Joined string of selected fragments
            and total character length.
    """
    selected_fragments = []
    total_length = initial_length
    for fragment in fragments:
        if total_length + len(fragment) <= max_tokens:
            selected_fragments.append(fragment)
            total_length += len(fragment)
        else:
            break
    return "\n\n".join(selected_fragments), total_length
