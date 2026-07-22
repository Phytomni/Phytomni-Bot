# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Stable source, symbol, and cross-file finding fingerprints."""

from __future__ import annotations

import ast
import hashlib
import io
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

DefinitionContext = tuple[int, int, str, ast.AST]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A normalized source endpoint participating in a finding identity."""

    path: str
    symbol: str | None
    normalized_source: str


def _definition_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    return None


def definition_contexts(tree: ast.AST) -> tuple[DefinitionContext, ...]:
    """Return qualified definition spans from an already parsed tree."""
    matches: list[DefinitionContext] = []

    def visit(node: ast.AST, parents: tuple[str, ...], depth: int) -> None:
        name = _definition_name(node)
        next_parents = parents
        next_depth = depth
        if name is not None:
            next_parents = (*parents, name)
            next_depth += 1
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and start <= end
            ):
                matches.append(
                    (next_depth, end - start, ".".join(next_parents), node)
                )
        for child in ast.iter_child_nodes(node):
            visit(child, next_parents, next_depth)

    visit(tree, (), 0)
    return tuple(matches)


def containing_symbol(source: str, line: int) -> str | None:
    """Return the innermost qualified definition containing ``line``.

    Display line numbers are used only for locating a symbol. They are not
    included in the resulting content fingerprint, so unrelated insertions do
    not invalidate an authorization.
    """
    if line < 1:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    matches = tuple(
        item
        for item in definition_contexts(tree)
        if item[0]
        and getattr(item[3], "lineno", 0)
        <= line
        <= getattr(item[3], "end_lineno", 0)
    )
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], -item[1]))[2]


def normalize_source(text: str) -> str:
    """Normalize Python tokens while retaining executable source identity."""
    tokens: list[str] = []
    ignored = {tokenize.COMMENT, tokenize.NL, tokenize.ENDMARKER}
    try:
        stream = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in stream:
            if token.type in ignored:
                continue
            if token.type == tokenize.ENCODING:
                continue
            tokens.append(f"{token.type}:{token.string}")
    except (IndentationError, SyntaxError, tokenize.TokenError) as exc:
        raise ValueError(f"cannot tokenize source: {exc}") from exc
    return " ".join(tokens)


def content_fingerprint(parts: Iterable[str]) -> str:
    """Hash length-delimited UTF-8 parts with a stable SHA-256 prefix."""
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def _endpoint_key(endpoint: Endpoint) -> tuple[str, str, str]:
    return (endpoint.path, endpoint.symbol or "", endpoint.normalized_source)


def _endpoint_parts(endpoint: Endpoint) -> tuple[str, str, str]:
    return _endpoint_key(endpoint)


def pair_fingerprint(left: Endpoint, right: Endpoint) -> str:
    """Fingerprint two endpoints in canonical lexical order."""
    return content_fingerprint(
        part
        for endpoint in sorted((left, right), key=_endpoint_key)
        for part in _endpoint_parts(endpoint)
    )


def _mechanism_value(mechanism: object) -> str:
    value = getattr(mechanism, "value", mechanism)
    if not isinstance(value, str):
        raise TypeError("mechanism must be a string or string enum")
    return value


def finding_fingerprint(
    tool: str,
    rule: str,
    mechanism: object,
    endpoints: Sequence[Endpoint],
    normalized_message: str,
) -> str:
    """Fingerprint a complete diagnostic identity and its target endpoints."""
    parts = [tool, rule, _mechanism_value(mechanism), normalized_message]
    for endpoint in sorted(endpoints, key=_endpoint_key):
        parts.extend(_endpoint_parts(endpoint))
    return content_fingerprint(parts)
