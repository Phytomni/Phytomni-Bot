# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Template, prompt, and file-loading helpers.

Functions: load_template, file_cache_fingerprint, load_json_file,
    load_text_file, render_template, get_prompt.

The renderer also supports two opt-in markers (no-ops on text without
them): ``{{> bucket/path}}`` includes another prompt body, and
``{{#if}}``/``{{#unless}}`` conditionals; both are reserved tokens.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from re import compile as _compile_re
from re import sub
from typing import Any
from warnings import warn

from yaml import safe_load

# Opt-in template markers. ``_INCLUDE_RE`` is resolved against the parsed
# YAML at load time; ``_CONDITIONAL_RE`` is resolved against runtime
# parameters at render time. Neither pattern matches a plain ``{{var}}``,
# so a template without these markers renders byte-identically.
_INCLUDE_RE = _compile_re(r"\{\{>\s*([^}]+?)\s*\}\}")
_CONDITIONAL_RE = _compile_re(
    r"\{\{#(?P<kind>if|unless)\s+(?P<var>[^}]+?)\s*\}\}"
    r"|\{\{/(?P<close>if|unless)\s*\}\}"
)
_MAX_INCLUDE_DEPTH = 20


def load_template(
    template_file: str,
    template_str: str | None = None,
) -> str:
    """Load a template string from a YAML file.

    Args:
        template_file: Path to the YAML template file.
        template_str: Optional dot-separated path to extract a nested value
            from the template (e.g., "section.subsection").

    Returns:
        str: Template string content, or extracted nested value
            if template_str provided.

    Raises:
        FileNotFoundError: If template file does not exist.
        KeyError: If template_str path not found in template structure.
        ValueError: If template_str provided but template has multiple
            top-level keys.
    """
    template_path = Path(template_file)
    if not template_path.is_file():
        raise FileNotFoundError(f"Template file not found: {template_file}")
    file_path, mtime_ns, size = file_cache_fingerprint(template_file)
    return _load_template_cached(
        file_path,
        template_str,
        mtime_ns,
        size,
    )


def file_cache_fingerprint(file_path: str) -> tuple[str, int, int]:
    """Return a stable file cache fingerprint for read-only local files.

    Args:
        file_path: Path to the file to fingerprint.

    Returns:
        tuple[str, int, int]: Resolved absolute path, modification time
            in nanoseconds, and file size in bytes.

    Raises:
        FileNotFoundError: If file does not exist.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


def _walk_prompt_path(data: Any, template_str: str) -> Any:
    """Walk a ``/``-separated path into the parsed template structure.

    Args:
        data: Parsed YAML structure (typically a nested dict).
        template_str: Dot/slash-separated path such as ``user/foo``.

    Returns:
        Any: The value at that path (a leaf string or a nested container).

    Raises:
        KeyError: If any path component is absent or walks past a leaf.
    """
    current = data
    for part in template_str.split("/"):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Path '{part}' not found in template structure")
        current = current[part]
    return current


def _expand_includes(text: str, data: Any, stack: tuple[str, ...]) -> str:
    """Recursively replace ``{{> path}}`` markers with referenced bodies.

    Partials are resolved within the same parsed ``data`` (one YAML file).
    A path already on ``stack`` is a cycle, and recursion depth is bounded,
    so a malformed include set fails loudly instead of recursing forever.

    Args:
        text: Template body that may contain ``{{> path}}`` markers.
        data: Parsed YAML structure the partials are looked up in.
        stack: Include paths currently being expanded, for cycle detection.

    Returns:
        str: ``text`` with every include replaced by its (expanded) body.

    Raises:
        ValueError: On an include cycle, depth overflow, or container target.
        KeyError: If an included path is absent from the structure.
    """
    if "{{>" not in text:
        return text
    if len(stack) > _MAX_INCLUDE_DEPTH:
        raise ValueError("Prompt include depth exceeded; check for a cycle")

    def replacer(match: Any) -> str:
        """Expand one ``{{> path}}`` marker into its partial body.

        Args:
            match: Regex match for a single include marker.

        Returns:
            str: The referenced partial, recursively include-expanded.
        """
        path = match.group(1).strip()
        if path in stack:
            raise ValueError(f"Cyclic prompt include detected: {path}")
        partial = _walk_prompt_path(data, path)
        if not isinstance(partial, str):
            raise ValueError(
                f"Prompt include '{path}' resolves to a container, "
                "not a text leaf"
            )
        return _expand_includes(partial, data, stack + (path,))

    return _INCLUDE_RE.sub(replacer, text)


def _load_template_cached(
    template_file: str,
    template_str: str | None,
    mtime_ns: int,
    size: int,
) -> str:
    """Load a template string from disk, expanding ``{{> path}}`` includes."""
    del mtime_ns, size
    with open(template_file, encoding="utf-8") as f:
        data = safe_load(f)
    if template_str:
        current = _walk_prompt_path(data, template_str)
        if isinstance(current, str):
            return _expand_includes(current, data, (template_str,))
        return current
    if isinstance(data, dict) and len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, str):
            return _expand_includes(only, data, ())
        return only
    raise ValueError("Must specify template_str for multi-level templates")


def load_json_file(file_path: str) -> Any:
    """Load a JSON file from disk.

    Args:
        file_path: Path to the JSON file to load.

    Returns:
        Any: Parsed JSON content (dict, list, or primitive type).

    Raises:
        FileNotFoundError: If file does not exist.
    """
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_json_file_cached(cache_path, mtime_ns, size)


def _load_json_file_cached(file_path: str, mtime_ns: int, size: int) -> Any:
    """Load JSON from disk."""
    del mtime_ns, size
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


def load_text_file(file_path: str) -> str:
    """Load a text file from disk.

    Args:
        file_path: Path to the text file to load.

    Returns:
        str: Full file contents.

    Raises:
        FileNotFoundError: If file does not exist.
    """
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_text_file_cached(cache_path, mtime_ns, size)


def _load_text_file_cached(file_path: str, mtime_ns: int, size: int) -> str:
    """Load text from disk."""
    del mtime_ns, size
    with open(file_path, encoding="utf-8") as f:
        return f.read()


def _is_truthy(value: Any) -> bool:
    """Return whether a conditional parameter is present and non-empty.

    Drives ``{{#if}}`` / ``{{#unless}}`` branch selection: ``None`` is
    falsy, a string is truthy when non-blank, a collection when non-empty,
    and any other value falls back to ``bool()``.

    Args:
        value: Parameter value looked up for a conditional marker.

    Returns:
        bool: ``True`` when the value counts as present and non-empty.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return bool(value)


def _resolve_conditionals(template: str, parameters: Mapping[str, Any]) -> str:
    """Resolve ``{{#if}}`` / ``{{#unless}}`` blocks against parameters.

    Runs before the flat ``{{var}}`` pass so a dropped branch never warns
    about variables it contains. A stack scanner supports nesting; an
    unbalanced, mismatched, or unclosed marker raises ``ValueError``.
    Returns the template unchanged when neither a ``{{#`` open nor a
    ``{{/`` close marker is present, so marker-free templates render
    byte-identically.

    Args:
        template: Template body that may contain conditional markers.
        parameters: Runtime values whose truthiness selects each branch.

    Returns:
        str: ``template`` with each conditional block kept or dropped.

    Raises:
        ValueError: On a stray, mismatched, or unclosed conditional marker.
    """
    if "{{#" not in template and "{{/" not in template:
        return template
    frames: list[list[str]] = [[]]
    keep_stack: list[tuple[str, bool]] = []
    pos = 0
    for match in _CONDITIONAL_RE.finditer(template):
        frames[-1].append(template[pos : match.start()])
        pos = match.end()
        kind = match.group("kind")
        if kind is not None:
            var = (match.group("var") or "").strip()
            truthy = _is_truthy(parameters.get(var))
            keep_stack.append((kind, truthy if kind == "if" else not truthy))
            frames.append([])
            continue
        if not keep_stack:
            raise ValueError(
                "Unbalanced conditional: stray close marker for "
                f"'{match.group('close')}'"
            )
        open_kind, keep = keep_stack.pop()
        close_kind = match.group("close") or ""
        if close_kind != open_kind:
            raise ValueError(
                f"Mismatched conditional: opened with '{open_kind}', "
                f"closed with '{close_kind}'"
            )
        block = "".join(frames.pop())
        if keep:
            frames[-1].append(block)
    frames[-1].append(template[pos:])
    if keep_stack:
        raise ValueError("Unclosed conditional block in template")
    return "".join(frames[0])


def render_template(
    template: str, parameters: Mapping[str, Any] | None = None
) -> str:
    """Replace placeholders in a template string with provided values.

    Resolves ``{{#if}}`` / ``{{#unless}}`` conditional blocks first, then
    substitutes flat ``{{placeholder}}`` tokens. The conditional and
    include tokens are reserved: a prompt body must not contain a bare
    ``{{#if}}`` / ``{{/if}}`` / ``{{#unless}}`` / ``{{/unless}}`` as
    literal prose, since an unbalanced marker raises rather than rendering
    as text. Document the marker syntax outside prompt bodies.

    Args:
        template: Template string with {{placeholder}} syntax.
        parameters: Optional dict of parameter values to substitute.
            Missing parameters trigger a warning and result in
            empty string replacement.

    Returns:
        str: Template with all placeholders replaced by parameter values.
    """
    if parameters is None:
        parameters = {}
    template = _resolve_conditionals(template, parameters)
    pattern = r"\{\{([^}]+)\}\}"

    def replacer(match):
        """Return a replacement value for one template placeholder.

        Args:
            match: Regular expression match for a `{{placeholder}}` token.

        Returns:
            Replacement text from parameters, or an empty string when missing.
        """
        param_name = match.group(1).strip()
        if param_name not in parameters:
            warn(f"Missing parameter '{param_name}' in template")
            return ""
        return str(parameters[param_name])

    return sub(pattern, replacer, template)


def get_prompt(
    template_file: str,
    template_str: str | None = None,
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Generate a complete prompt from a template file and parameters.

    Args:
        template_file: Path to the YAML template file.
        template_str: Optional dot-separated path to extract nested value.
        parameters: Optional dict of values to substitute into placeholders.

    Returns:
        str: Rendered prompt with all placeholders replaced.
    """
    if parameters is None:
        parameters = {}
    template = load_template(template_file, template_str)
    return render_template(template, parameters)
