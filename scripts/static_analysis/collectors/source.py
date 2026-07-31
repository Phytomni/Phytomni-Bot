# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Collect exact inline suppression directives from Python token streams."""

from __future__ import annotations

import ast
import io
import re
import textwrap
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..fingerprints import (
    Endpoint,
    definition_contexts,
    finding_fingerprint,
    normalize_source,
)
from ..model import Finding, Mechanism, TargetKind

_PYLINT_RE = re.compile(
    r"^pylint\s*:\s*disable(?P<next>-next)?\s*=\s*(?P<rules>.*)$",
    re.IGNORECASE,
)
_NOQA_RE = re.compile(
    r"^(?:(?P<tool>ruff|flake8)\s*:\s*)?noqa" + r"(?:\s*:\s*(?P<rules>.*))?$",
    re.IGNORECASE,
)
_TYPE_IGNORE_RE = re.compile(
    r"^type\s*:\s*ignore(?:\[(?P<rules>[^]]*)\])?$",
    re.IGNORECASE,
)
_PYRIGHT_IGNORE_RE = re.compile(
    r"^pyright\s*:\s*ignore(?:\[(?P<rules>[^]]*)\])?$",
    re.IGNORECASE,
)
_MYPY_IGNORE_RE = re.compile(
    r"^mypy\s*:\s*ignore(?:-errors)?(?:\[(?P<rules>[^]]*)\])?$",
    re.IGNORECASE,
)
_NOSEC_RE = re.compile(r"^nosec(?:\s+(?P<rules>.*))?$", re.IGNORECASE)
_SECRET_PRAGMA = "pragma: allowlist secret"


@dataclass(frozen=True, slots=True)
class _Directive:
    """Parsed suppression metadata independent of source location."""

    tool: str
    mechanism: Mechanism
    rules: tuple[str, ...]
    applies_next: bool


@dataclass(frozen=True, slots=True)
class _SourceContext:
    """Source file context shared by findings from one token stream."""

    root: Path
    path: Path
    source: str


def _relative_path(root: Path, path: Path) -> str:
    candidate = path if path.is_absolute() else root / path
    return candidate.resolve().relative_to(root.resolve()).as_posix()


def _symbol_context(source: str, line: int) -> tuple[str | None, str]:
    """Return the innermost symbol and normalized source containing a line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, _normalize_unbound_line(source, line)

    matches = tuple(
        item
        for item in definition_contexts(tree)
        if getattr(item[3], "lineno", 0)
        <= line
        <= getattr(item[3], "end_lineno", 0)
    )
    if not matches:
        return None, _normalize_unbound_line(source, line)
    _, _, symbol, node = max(matches, key=lambda item: (item[0], -item[1]))
    segment = ast.get_source_segment(source, node) or ""
    return symbol, normalize_source(segment)


def _next_definition_context(source: str, line: int) -> tuple[str | None, str]:
    """Bind a Pylint directive before a decorator to its next definition.

    Pylint comments commonly sit between a decorator and the ``def`` line.
    The normal containing-symbol lookup cannot see through that gap and used
    to collapse repeated directives into one module-level span.  Only a
    decorated definition with a blank/comment/decorator bridge is accepted,
    so a true module directive followed by imports or a plain definition
    remains a span target.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, ""

    lines = source.splitlines()
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and isinstance(getattr(node, "lineno", None), int)
        and getattr(node, "lineno") > line
    ]
    for node in sorted(candidates, key=lambda item: item.lineno):
        start = node.lineno
        decorators = getattr(node, "decorator_list", ())
        if not decorators:
            continue
        has_adjacent_decorator = any(
            getattr(decorator, "end_lineno", 0) <= line
            or getattr(decorator, "lineno", 0) > line
            for decorator in decorators
        )
        if not has_adjacent_decorator:
            continue
        bridge = lines[slice(line, start - 1)]
        if all(
            not text.strip()
            or text.lstrip().startswith("#")
            or text.lstrip().startswith("@")
            for text in bridge
        ):
            symbol, normalized_source = _symbol_context(source, start)
            if symbol is not None:
                return symbol, normalized_source
    return None, ""


def _normalize_unbound_line(source: str, line: int) -> str:
    """Normalize an indented line without leaking tokenizer errors."""
    lines = source.splitlines()
    line_text = lines[line - 1] if 0 < line <= len(lines) else ""
    try:
        return normalize_source(line_text)
    except ValueError:
        try:
            return normalize_source(textwrap.dedent(line_text))
        except ValueError:
            return line_text.strip()


def _rules(raw_rules: str | None) -> tuple[str, ...]:
    if raw_rules is None:
        return ("*",)
    values = tuple(item.strip() for item in raw_rules.split(","))
    selected = tuple(item for item in values if item)
    return selected or ("*",)


def _parse_pylint(text: str) -> _Directive | None:
    match = _PYLINT_RE.fullmatch(text)
    if match is None:
        return None
    return _Directive(
        "pylint",
        Mechanism.INLINE,
        _rules(match.group("rules")),
        match.group("next") is not None,
    )


def _parse_noqa(text: str) -> _Directive | None:
    match = _NOQA_RE.fullmatch(text)
    if match is None:
        return None
    return _Directive(
        (match.group("tool") or "ruff").lower(),
        Mechanism.INLINE,
        _rules(match.group("rules")),
        False,
    )


def _parse_typing_ignore(text: str) -> _Directive | None:
    match = _TYPE_IGNORE_RE.fullmatch(text)
    if match is not None:
        return _Directive(
            "mypy", Mechanism.INLINE, _rules(match.group("rules")), False
        )
    match = _PYRIGHT_IGNORE_RE.fullmatch(text)
    if match is not None:
        return _Directive(
            "pyright", Mechanism.INLINE, _rules(match.group("rules")), False
        )
    match = _MYPY_IGNORE_RE.fullmatch(text)
    if match is not None:
        return _Directive(
            "mypy", Mechanism.INLINE, _rules(match.group("rules")), False
        )
    return None


def _parse_secret_marker(text: str) -> _Directive | None:
    match = _NOSEC_RE.fullmatch(text)
    if match is not None:
        return _Directive(
            "secret-scan",
            Mechanism.MARKER,
            _rules(match.group("rules")),
            False,
        )
    if text.lower() == _SECRET_PRAGMA:
        return _Directive("secret-scan", Mechanism.MARKER, ("*",), False)
    return None


def _parse_comment(comment: str) -> _Directive | None:
    text = comment.lstrip("#").strip()
    for parser in (
        _parse_pylint,
        _parse_noqa,
        _parse_typing_ignore,
        _parse_secret_marker,
    ):
        directive = parser(text)
        if directive is not None:
            return directive
    return None


def _finding(
    context: _SourceContext,
    line: int,
    comment: str,
    directive: _Directive,
    rule: str,
) -> Finding:
    target_line = line + 1 if directive.applies_next else line
    symbol, normalized_source = _symbol_context(context.source, target_line)
    if symbol is None and directive.applies_next:
        symbol, normalized_source = _symbol_context(context.source, line)
    if symbol is None and directive.tool == "pylint":
        symbol, normalized_source = _next_definition_context(
            context.source, line
        )
    if symbol is None and not normalized_source:
        normalized_source = _normalize_unbound_line(context.source, line)
    display_path = _relative_path(context.root, context.path)
    target_kind = TargetKind.SYMBOL if symbol else TargetKind.SPAN
    endpoint = Endpoint(display_path, symbol, normalized_source)
    fingerprint = finding_fingerprint(
        directive.tool,
        rule,
        directive.mechanism,
        (endpoint,),
        comment.lstrip("#").strip(),
    )
    return Finding(
        tool=directive.tool,
        rule=rule,
        mechanism=directive.mechanism,
        target_kind=target_kind,
        path=display_path,
        symbol=symbol,
        peer_path=None,
        peer_symbol=None,
        fingerprint=fingerprint,
        location=f"{display_path}:{line}",
        message=comment.lstrip("#").strip(),
        tool_version=None,
    )


def _collect_file(root: Path, path: Path) -> Iterator[Finding]:
    source = path.read_text(encoding="utf-8")
    context = _SourceContext(root, path, source)
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        parsed = _parse_comment(token.string)
        if parsed is None:
            continue
        for rule in parsed.rules:
            yield _finding(
                context,
                token.start[0],
                token.string,
                parsed,
                rule,
            )


def collect_source_suppressions(
    root: Path, paths: Sequence[Path]
) -> tuple[Finding, ...]:
    """Collect every supported exact directive from tracked Python files."""
    findings: list[Finding] = []
    for path in sorted(paths, key=lambda item: _relative_path(root, item)):
        if path.suffix not in {".py", ".pyi"}:
            continue
        findings.extend(_collect_file(root, path))
    return tuple(findings)
