# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Golden snapshot of every prompt's rendered output.

Rendering each tracked prompt through the full load + render pipeline with
deterministic ``<var>`` sentinels and pinning the result to a committed
baseline turns any later corpus or renderer change into a visible diff.
Consolidating prompt bodies via includes must keep this snapshot
byte-identical; regenerate intentionally with
``PHYTOMNI_REGEN_PROMPT_GOLDEN=1 uv run pytest`` and review the diff.
"""

import json
import os
import re
from pathlib import Path

import pytest
import yaml

from mcp_server_phytomni.common.prompts import load_template, render_template

pytestmark = pytest.mark.unit

_PROMPT_FILE = (
    Path(__file__)
    .resolve()
    .parents[3]
    .joinpath("src", "mcp_server_phytomni", "config", ".prompts.yaml")
)
_GOLDEN_FILE = Path(__file__).resolve().parent / "prompt_render_golden.json"
_VAR_RE = re.compile(r"\{\{([^}]+)\}\}")
_REGEN_ENV = "PHYTOMNI_REGEN_PROMPT_GOLDEN"


def _leaf_paths(root):
    """Yield ``a/b/c`` paths for every non-dict leaf, walking iteratively."""
    pending: list[tuple[tuple[str, ...], object]] = [((), root)]
    while pending:
        prefix, node = pending.pop()
        if not isinstance(node, dict):
            yield "/".join(prefix)
            continue
        for key, value in node.items():
            pending.append((prefix + (key,), value))


def _render_map():
    """Render every prompt leaf through load + render with sentinel params.

    Vars are collected from the include-expanded template (what
    ``load_template`` returns) so a body shared via ``{{> partial}}`` still
    renders identically. Marker captures (``#``/``/``/``>`` leaders) are not
    treated as substitutable variables. ``user/_partials/*`` bodies are
    internal include targets, never loaded directly by an agent; their
    content is already pinned transitively through the hosts that include
    them, so they are skipped to keep this baseline a snapshot of
    directly-sent prompts only.
    """
    data = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))
    rendered = {}
    for path in _leaf_paths(data):
        if "/_partials/" in path:
            continue
        loaded = load_template(str(_PROMPT_FILE), path)
        names = {
            match.group(1).strip()
            for match in _VAR_RE.finditer(loaded)
            if match.group(1).strip()[:1] not in ("#", "/", ">")
        }
        params = {name: f"<{name}>" for name in names}
        rendered[path] = render_template(loaded, params)
    return rendered


def test_every_prompt_renders_to_the_golden_baseline():
    """Verify every prompt renders byte-identically to the pinned baseline."""
    current = _render_map()
    if os.environ.get(_REGEN_ENV) == "1":
        _GOLDEN_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        pytest.skip("Regenerated prompt render baseline")
    assert _GOLDEN_FILE.is_file(), (
        f"Missing {_GOLDEN_FILE.name}; regenerate with "
        f"{_REGEN_ENV}=1 uv run pytest {Path(__file__).name}"
    )
    golden = json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))
    assert current == golden
