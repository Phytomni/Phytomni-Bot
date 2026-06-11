# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Template, prompt, and file-loading helpers.

Functions: load_template, file_cache_fingerprint, load_json_file,
    load_text_file, render_template, get_prompt.
"""

import json
from pathlib import Path
from re import sub
from typing import Any, Mapping, Optional
from warnings import warn

from yaml import safe_load


def load_template(
    template_file: str,
    template_str: Optional[str] = None,
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


def _load_template_cached(
    template_file: str,
    template_str: Optional[str],
    mtime_ns: int,
    size: int,
) -> str:
    """Load a template string from disk."""
    del mtime_ns, size
    with open(template_file, "r", encoding="utf-8") as f:
        data = safe_load(f)
    if template_str:
        current = data
        for part in template_str.split("/"):
            if part not in current:
                raise KeyError(
                    f"Path '{part}' not found in template structure"
                )
            current = current[part]
        return current
    if isinstance(data, dict) and len(data) == 1:
        return next(iter(data.values()))
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
    with open(file_path, "r", encoding="utf-8") as f:
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
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(
    template: str, parameters: Optional[Mapping[str, Any]] = None
) -> str:
    """Replace placeholders in a template string with provided values.

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
    template_str: Optional[str] = None,
    parameters: Optional[Mapping[str, Any]] = None,
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
