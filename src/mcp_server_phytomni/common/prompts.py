# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Template, prompt, and cached file-loading helpers."""

import json
from pathlib import Path
from re import sub
from typing import Any, Mapping, Optional
from warnings import warn

from yaml import safe_load

from ..func_cache import func_cache

FILE_CACHE_TTL = 3600


def load_template(
    template_file: str,
    template_str: Optional[str] = None,
) -> str:
    """Load a template string from a YAML file."""
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
    """Return a stable file cache fingerprint for read-only local files."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@func_cache(
    key_params=["template_file", "template_str", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_template_cached(
    template_file: str,
    template_str: Optional[str],
    mtime_ns: int,
    size: int,
) -> str:
    """Load a template string from disk using a file-aware cache key."""
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
    """Load a JSON file through the shared file-aware cache."""
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_json_file_cached(cache_path, mtime_ns, size)


@func_cache(
    key_params=["file_path", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_json_file_cached(file_path: str, mtime_ns: int, size: int) -> Any:
    """Load JSON from disk using a file-aware cache key."""
    del mtime_ns, size
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text_file(file_path: str) -> str:
    """Load a text file through the shared file-aware cache."""
    cache_path, mtime_ns, size = file_cache_fingerprint(file_path)
    return _load_text_file_cached(cache_path, mtime_ns, size)


@func_cache(
    key_params=["file_path", "mtime_ns", "size"],
    ttl=FILE_CACHE_TTL,
)
def _load_text_file_cached(file_path: str, mtime_ns: int, size: int) -> str:
    """Load text from disk using a file-aware cache key."""
    del mtime_ns, size
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def render_template(
    template: str, parameters: Optional[Mapping[str, Any]] = None
) -> str:
    """Replace placeholders in a template string with provided values."""
    if parameters is None:
        parameters = {}
    pattern = r"\{\{([^}]+)\}\}"

    def replacer(match):
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
    """Generate a complete prompt from a template file and parameters."""
    if parameters is None:
        parameters = {}
    template = load_template(template_file, template_str)
    return render_template(template, parameters)
