# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Prompt context formatting helpers for uploads and retrieved documents.

Functions: format_upload_context, format_retrieved_doc_context,
    format_retrieved_doc_fragment.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from .responses import join_limited_fragments


def format_upload_context(
    upload_texts: Iterable[str],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Format uploaded file texts as bounded prompt context.

    Args:
        upload_texts: Iterable of text content from uploaded files.
        max_tokens: Maximum combined character length allowed.
        initial_length: Starting length before processing (default 0).

    Returns:
        tuple[str, int]: Formatted context string with file markers
        and total length.
    """
    fragments = (
        f"[user upload file {index + 1} begin]\n"
        f"{text}\n[user upload file {index + 1} end]"
        for index, text in enumerate(upload_texts)
    )
    return join_limited_fragments(
        fragments,
        max_tokens=max_tokens,
        initial_length=initial_length,
    )


def format_retrieved_doc_context(
    docs: Iterable[Mapping[str, Any]],
    max_tokens: int,
    initial_length: int = 0,
) -> tuple[str, int]:
    """Format retrieved documents as bounded prompt context.

    Args:
        docs: Iterable of document dicts with 'title', 'content',
            and optional 'subtitle'.
        max_tokens: Maximum combined character length allowed.
        initial_length: Starting length before processing (default 0).

    Returns:
        tuple[str, int]: Formatted context string with document markers
        and total length.
    """
    fragments = (
        format_retrieved_doc_fragment(doc, index)
        for index, doc in enumerate(docs)
    )
    return join_limited_fragments(
        fragments,
        max_tokens=max_tokens,
        initial_length=initial_length,
    )


def format_retrieved_doc_fragment(
    doc: Mapping[str, Any],
    index: int,
    label: str = "document",
) -> str:
    """Format one retrieved document fragment for prompt context.

    Args:
        doc: Document dict with 'title' and 'content' or 'big_content' fields.
        index: Zero-based index for numbering the fragment.
        label: Label for the fragment type (default 'document').

    Returns:
        str: Formatted fragment with title, body, and markers.
    """
    header = f"[{label} {index + 1} begin] {doc['title']}"
    content_field = (
        doc.get("big_content")
        if "big_content" in doc
        else doc.get("content", "")
    )
    body = (
        f"{doc['subtitle']}\n{content_field}"
        if doc.get("subtitle")
        else doc.get("content", "")
    )
    return f"{header}\n{body} [{label} {index + 1} end]"
