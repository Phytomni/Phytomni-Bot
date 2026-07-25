# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Pure helpers shared across the review workflow mixins.

JSON extraction, document fragment formatting, and citation
normalization helpers consumed by planning, report, and summary mixins
without going through the DeepResearchAgent class instance.
"""

from __future__ import annotations

import re
from json import loads
from typing import Any

from ...graphs.chat_adapters import build_chat_kwargs_for
from ...runtime.locale import SupportedLocale

CITATION_PATTERN = (
    r"\[(?:add )?document [^\]]+\]|\[[Ss]?\d+-\d{3}\]|\[[sS]?\d{3}\]"
)


def build_review_chat_kwargs(
    config: Any,
    sensitive_config: Any,
    locale: SupportedLocale | None,
    *,
    with_follow_up: bool = False,
) -> dict[str, Any]:
    """Build the shared review Chat options for one workflow node."""
    return build_chat_kwargs_for(
        config,
        sensitive_config,
        with_follow_up=with_follow_up,
        locale=locale,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output."""
    start_index = text.find("{")
    end_index = text.rfind("}") + 1
    if start_index == -1 or end_index <= start_index:
        return {}
    try:
        parsed = loads(text[start_index:end_index])
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _doc_content(doc: dict[str, Any]) -> str:
    """Return the best available document text field."""
    return str(doc.get("big_content") or doc.get("content") or "")


def _format_doc_fragment(doc: dict[str, Any], doc_id: str) -> str:
    """Format a retrieved document as a prompt fragment."""
    title = doc.get("title", "")
    subtitle = doc.get("subtitle", "")
    content = _doc_content(doc)
    body = f"{subtitle}\n{content}" if subtitle else content
    return f"[{doc_id} begin] {title}\n{body} [{doc_id} end]"


def _normalize_citation_id(raw_id: str) -> str:
    """Normalize short citation aliases to internal document IDs."""
    if re.match(r"^[Ss]\d+-\d{3}$", raw_id):
        return f"add document {raw_id.upper()}"
    if re.match(r"^\d{3}$", raw_id):
        return f"document {raw_id}"
    if re.match(r"^[Ss]\d{3}$", raw_id):
        return f"document {raw_id[1:]}"
    return raw_id


def _renumber_citations(
    summary_text: str, doc_list: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Convert internal citation IDs to public [document:N] references."""
    final_doc_lookup = {
        str(doc.get("doc_id", "")): doc
        for doc in doc_list
        if doc.get("doc_id")
    }
    raw_tags = list(dict.fromkeys(re.findall(CITATION_PATTERN, summary_text)))
    tag_to_number: dict[str, int] = {}
    ordered_doc_list: list[dict[str, Any]] = []
    current_ref_number = 1

    for tag in raw_tags:
        norm_id = _normalize_citation_id(tag.strip("[]"))
        norm_tag = f"[{norm_id}]"
        if norm_tag in tag_to_number:
            continue
        tag_to_number[norm_tag] = current_ref_number
        if norm_id in final_doc_lookup:
            doc_copy = final_doc_lookup[norm_id].copy()
            doc_copy["doc_id"] = current_ref_number
            ordered_doc_list.append(doc_copy)
        else:
            ordered_doc_list.append(
                {
                    "doc_id": current_ref_number,
                    "title": "Unknown Document",
                    "content": "Content missing due to invalid reference.",
                }
            )
        current_ref_number += 1

    def replace_with_number(match: re.Match[str]) -> str:
        norm_id = _normalize_citation_id(match.group(0).strip("[]"))
        ref_number = tag_to_number.get(f"[{norm_id}]", "?")
        return f"[document:{ref_number}]"

    formatted_text = re.sub(
        CITATION_PATTERN, replace_with_number, summary_text
    )

    def sort_citation_block(match: re.Match[str]) -> str:
        nums = [
            int(num)
            for num in re.findall(r"\[document:(\d+)\]", match.group(0))
        ]
        return "".join(f"[document:{num}]" for num in sorted(set(nums)))

    formatted_text = re.sub(
        r"(?:\[document:\d+\][\s,]*){2,}",
        sort_citation_block,
        formatted_text,
    )
    return formatted_text, ordered_doc_list
