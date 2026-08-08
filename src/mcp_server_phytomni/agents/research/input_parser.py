# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Strict, side-effect-free parsing of pasted Research dataset references."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .input_contracts import (
    ParsedResearchInput,
    PastedDatasetCandidate,
    ResearchInputFailure,
    SourceSpan,
)
from .scientific_formats import classify_scientific_reference

__all__ = ["has_explicit_research_data_syntax", "parse_research_input"]

_DATA_LABEL = re.compile(r"(?<![A-Za-z0-9_])[dD][aA][tT][aA]:")
_JSON_KEY = re.compile(r'"((?:[^"\\\x00-\x1f]|\\.)*)"[ \t\r\n]*:')
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_KEY_SEGMENT = re.compile(r"^[\w.-]+$", re.UNICODE)


@dataclass(frozen=True, slots=True)
class _RawCandidate:
    """Validated parser-local candidate before ordinal assignment."""

    exact_reference: str
    comparison_key: str
    user_hint: str | None
    source_start: int
    source_end: int


class _DuplicateJsonKeyError(ValueError):
    """Raised by the strict JSON object-pairs hook."""


def parse_research_input(
    original_query: str, bucket: str
) -> ParsedResearchInput:
    """Parse the three approved pasted-data grammars without any I/O.

    Only fully recognized spans are removed.  All returned positions are Python
    code-point offsets into ``original_query``; reference normalization is used
    solely for comparison and never rewrites the caller's exact reference.
    """
    spans, raw_candidates = _parse_data_blocks(original_query, bucket)
    standalone_spans, standalone_candidates = _parse_standalone_lines(
        original_query, bucket, spans
    )
    spans.extend(standalone_spans)
    raw_candidates.extend(standalone_candidates)
    spans.sort(key=lambda span: span.start)
    raw_candidates.sort(key=lambda candidate: candidate.source_start)

    _reject_duplicates(raw_candidates)
    candidates = tuple(
        PastedDatasetCandidate(
            exact_reference=candidate.exact_reference,
            comparison_key=candidate.comparison_key,
            user_hint=candidate.user_hint,
            source_start=candidate.source_start,
            source_end=candidate.source_end,
            ordinal=ordinal,
        )
        for ordinal, candidate in enumerate(raw_candidates)
    )
    effective_query, effective_to_original = _remove_spans(
        original_query, spans
    )
    return ParsedResearchInput(
        original_query_digest=hashlib.sha256(
            original_query.encode("utf-8")
        ).hexdigest(),
        original_query_length=len(original_query),
        effective_query=effective_query,
        effective_to_original=effective_to_original,
        removed_spans=tuple(spans),
        candidates=candidates,
    )


def has_explicit_research_data_syntax(query: str, bucket: str) -> bool:
    """Return whether query selects the strict data-input grammar.

    The probe has no I/O and exposes neither paths nor candidates.  Invalid
    associated ``data:`` syntax remains explicit so callers can select the
    strict failure path instead of treating it as ordinary prose.
    """
    for label in _DATA_LABEL.finditer(query):
        after_label = _skip_whitespace(query, label.end())
        if after_label < len(query) and (
            query[after_label] == "{" or query.startswith("```", after_label)
        ):
            return True
    try:
        return bool(parse_research_input(query, bucket).candidates)
    except ResearchInputFailure:
        return _looks_like_standalone_reference(query)


def _parse_data_blocks(
    query: str, bucket: str
) -> tuple[list[SourceSpan], list[_RawCandidate]]:
    """Collect every label-associated strict JSON block in source order."""
    spans: list[SourceSpan] = []
    candidates: list[_RawCandidate] = []
    for label in _DATA_LABEL.finditer(query):
        if any(span.start <= label.start() < span.end for span in spans):
            continue
        after_label = _skip_whitespace(query, label.end())
        if after_label < len(query) and query.startswith("```", after_label):
            span, block_candidates = _parse_fenced_json(
                query, label.start(), after_label, bucket
            )
        elif after_label < len(query) and query[after_label] == "{":
            span, block_candidates = _parse_trailing_json(
                query, label.start(), after_label, bucket
            )
        else:
            continue
        if any(_overlaps(span, existing) for existing in spans):
            continue
        spans.append(span)
        candidates.extend(block_candidates)
    return spans, candidates


def _parse_trailing_json(
    query: str, label_start: int, object_start: int, bucket: str
) -> tuple[SourceSpan, list[_RawCandidate]]:
    """Parse a strict object that is the terminal structured payload."""
    value, object_end = _decode_object(query, object_start)
    if query[object_end:].strip():
        raise _data_block_invalid()
    candidates = _object_candidates(
        query, object_start, object_end, value, bucket
    )
    return (
        SourceSpan(label_start, object_end, "trailing_json"),
        candidates,
    )


def _parse_fenced_json(
    query: str, label_start: int, fence_start: int, bucket: str
) -> tuple[SourceSpan, list[_RawCandidate]]:
    """Parse one ``data:``-associated fenced JSON object."""
    header_end = _line_end(query, fence_start)
    header = query[slice(fence_start + 3, header_end)].rstrip("\r")
    if header not in ("", "json", "JSON") or header_end == len(query):
        raise _data_block_invalid()
    body_start = header_end + (
        2 if query[slice(header_end, header_end + 2)] == "\r\n" else 1
    )
    close = _find_closing_fence(query, body_start)
    if close is None:
        raise _data_block_invalid()
    close_start, close_end = close
    object_start = _skip_whitespace(query, body_start)
    value, object_end = _decode_object(query, object_start)
    if query[object_end:close_start].strip():
        raise _data_block_invalid()
    trailing = query[close_end:].lstrip()
    if trailing.startswith(("{", "[", "```")):
        raise _data_block_invalid()
    candidates = _object_candidates(
        query, object_start, object_end, value, bucket
    )
    return (
        SourceSpan(label_start, close_end, "fenced_json"),
        candidates,
    )


def _decode_object(
    query: str, object_start: int
) -> tuple[dict[str, str], int]:
    """Decode one strict object and retain its exact end offset."""
    decoder = json.JSONDecoder(
        object_pairs_hook=_strict_object_pairs,
        parse_constant=_reject_json_constant,
    )
    try:
        value, object_end = decoder.raw_decode(query, object_start)
    except (json.JSONDecodeError, _DuplicateJsonKeyError, ValueError) as exc:
        raise _data_block_invalid() from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(description, str)
        for key, description in value.items()
    ):
        raise _data_block_invalid()
    return value, object_end


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON keys rather than silently keeping one value."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """Reject JavaScript numeric constants that strict JSON does not permit."""
    raise ValueError(f"invalid JSON constant: {value}")


def _object_candidates(
    query: str,
    object_start: int,
    object_end: int,
    value: dict[str, str],
    bucket: str,
) -> list[_RawCandidate]:
    """Create source-round-trippable candidates from one validated object."""
    raw_object = query[object_start:object_end]
    key_matches = tuple(_JSON_KEY.finditer(raw_object))
    if len(key_matches) != len(value):
        raise _data_block_invalid()
    candidates: list[_RawCandidate] = []
    for (reference, hint), match in zip(
        value.items(), key_matches, strict=True
    ):
        try:
            raw_reference = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError as exc:
            raise _data_block_invalid() from exc
        if raw_reference != reference or match.group(1) != reference:
            raise _data_block_invalid()
        source_start = object_start + match.start(1)
        source_end = object_start + match.end(1)
        candidates.append(
            _candidate(
                reference, hint or None, source_start, source_end, bucket
            )
        )
    return candidates


def _parse_standalone_lines(
    query: str, bucket: str, occupied: Iterable[SourceSpan]
) -> tuple[list[SourceSpan], list[_RawCandidate]]:
    """Parse complete LF/CRLF lines that are exact standalone references."""
    spans: list[SourceSpan] = []
    candidates: list[_RawCandidate] = []
    offset = 0
    for raw_line in query.splitlines(keepends=True) or [query]:
        line = raw_line.removesuffix("\r\n").removesuffix("\n")
        line_end = offset + len(line)
        line_span = SourceSpan(offset, line_end, "standalone_tab")
        if not any(_overlaps(line_span, span) for span in occupied):
            parsed = _parse_standalone_line(line, offset, bucket)
            if parsed is not None:
                candidate, span = parsed
                candidates.append(candidate)
                spans.append(span)
        offset += len(raw_line)
    return spans, candidates


def _parse_standalone_line(
    line: str, offset: int, bucket: str
) -> tuple[_RawCandidate, SourceSpan] | None:
    """Return one strict standalone candidate or leave ordinary prose alone."""
    if not line:
        return None
    if _starts_obs_reference(line):
        if line.count("\t") > 1:
            raise _path_invalid()
        reference, delimiter, hint = line.partition("\t")
        if not delimiter and not _is_complete_reference(reference):
            raise _path_invalid()
        if delimiter and not _is_complete_reference(reference):
            raise _path_invalid()
        candidate = _candidate(
            reference,
            hint or None,
            offset,
            offset + len(reference),
            bucket,
        )
        return candidate, SourceSpan(
            offset, offset + len(line), "standalone_tab"
        )
    if _looks_like_forbidden_standalone_path(line):
        raise _path_invalid()
    return None


def _candidate(
    reference: str,
    hint: str | None,
    source_start: int,
    source_end: int,
    bucket: str,
) -> _RawCandidate:
    """Validate one exact reference and derive its comparison-only key."""
    comparison_key = _comparison_key(reference, bucket)
    if comparison_key is None:
        raise _path_invalid()
    if classify_scientific_reference(reference) is None:
        raise _format_unsupported()
    return _RawCandidate(
        reference, comparison_key, hint, source_start, source_end
    )


def _comparison_key(reference: str, bucket: str) -> str | None:
    """Validate OBS authority and return a normalized comparison identity."""
    if any(
        ord(character) < 32 or ord(character) == 127 for character in reference
    ):
        return None
    match = re.fullmatch(r"obs://([^/]+)/(.+)", reference, flags=re.IGNORECASE)
    if match is None:
        return None
    reference_bucket, key = match.groups()
    if not bucket or reference_bucket.casefold() != bucket.casefold():
        return None
    segments = key.split("/")
    if not segments or any(
        segment in ("", ".", "..") or not _KEY_SEGMENT.fullmatch(segment)
        for segment in segments
    ):
        return None
    normalized_key = unicodedata.normalize("NFC", key)
    return f"obs://{bucket.lower()}/{normalized_key}"


def _is_complete_reference(reference: str) -> bool:
    """Keep line grammar strict before bucket-specific validation."""
    return bool(
        re.fullmatch(r"obs://[^/]+/.+", reference, flags=re.IGNORECASE)
    )


def _starts_obs_reference(line: str) -> bool:
    """Recognize a standalone OBS-looking line without inspecting prose."""
    return line[:6].casefold() == "obs://"


def _looks_like_forbidden_standalone_path(line: str) -> bool:
    """Detect a whole-line path authority that must fail rather than prose."""
    lowered = line.casefold()
    return bool(
        _WINDOWS_PATH.match(line)
        or line.startswith(("/", "\\\\"))
        or _SCHEME.match(line)
        or lowered.startswith(("file:", "s3:", "gs:"))
    )


def _looks_like_standalone_reference(query: str) -> bool:
    """Return a bounded path-shape signal for the selector probe."""
    return any(
        _starts_obs_reference(raw_line.removesuffix("\r\n").removesuffix("\n"))
        for raw_line in query.splitlines(keepends=True)
    )


def _find_closing_fence(query: str, start: int) -> tuple[int, int] | None:
    """Find the next standalone closing fence and retain its content end."""
    offset = start
    for raw_line in query[start:].splitlines(keepends=True):
        content = raw_line.removesuffix("\r\n").removesuffix("\n")
        if content == "```":
            return offset, offset + len(content)
        offset += len(raw_line)
    return None


def _line_end(query: str, start: int) -> int:
    """Return code-point offset before the next LF or query end."""
    newline = query.find("\n", start)
    return len(query) if newline < 0 else newline


def _skip_whitespace(query: str, offset: int) -> int:
    """Advance across whitespace while retaining exact original offsets."""
    while offset < len(query) and query[offset].isspace():
        offset += 1
    return offset


def _remove_spans(
    query: str, spans: Iterable[SourceSpan]
) -> tuple[str, tuple[int, ...]]:
    """Remove only full recognized spans and map retained code points back."""
    removed = [False] * len(query)
    for span in spans:
        for index in range(span.start, span.end):
            removed[index] = True
    indexes = tuple(
        index for index, is_removed in enumerate(removed) if not is_removed
    )
    return "".join(query[index] for index in indexes), indexes


def _reject_duplicates(candidates: Iterable[_RawCandidate]) -> None:
    """Reject duplicate comparison identities across every approved grammar."""
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.comparison_key in seen:
            raise _failure(
                "research_dataset_duplicate", "Research dataset is duplicated."
            )
        seen.add(candidate.comparison_key)


def _overlaps(left: SourceSpan, right: SourceSpan) -> bool:
    """Return whether two half-open source spans share any code point."""
    return left.start < right.end and right.start < left.end


def _data_block_invalid() -> ResearchInputFailure:
    """Build the stable failure for malformed explicit JSON syntax."""
    return _failure(
        "research_data_block_invalid", "Research data block is invalid."
    )


def _path_invalid() -> ResearchInputFailure:
    """Build the stable failure for invalid explicit path authority."""
    return _failure(
        "research_dataset_path_invalid", "Research dataset path is invalid."
    )


def _format_unsupported() -> ResearchInputFailure:
    """Build the stable failure for an unsupported deterministic suffix."""
    return _failure(
        "research_dataset_format_unsupported",
        "Research dataset format is unsupported.",
    )


def _failure(code: Any, safe_message: str) -> ResearchInputFailure:
    """Create a non-retryable domain failure for parser-owned validation."""
    return ResearchInputFailure(
        code=code,
        safe_message=safe_message,
        http_status_hint=400,
        stage="input_resolution",
        retryable=False,
    )
