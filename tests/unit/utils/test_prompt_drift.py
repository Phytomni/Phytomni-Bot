# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Drift guard binding the tracked ``.prompts.yaml`` to the code.

The ``tests/agents/*`` suites monkeypatch ``get_prompt`` and the sibling
``test_prompt.py`` only uses synthetic YAML, so nothing else loads the
real prompt file. This closes the gap both ways: every prompt path the
code can load must resolve to a leaf *string* (forward), and every YAML
leaf must be referenced or allow-listed (inverse), so a key cannot
silently rot and a missing key cannot crash an agent unnoticed.
"""

import re
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

from mcp_server_phytomni.agents.deep_genome.dispatch import (
    ANALYSIS_GOAL_TEMPLATE_MAP,
    ANALYSIS_META_TEMPLATE_MAP,
)
from mcp_server_phytomni.agents.deep_genome.report import (
    _SYNTHESIS_SECTIONS,
)
from mcp_server_phytomni.common.prompts import load_template

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "mcp_server_phytomni"
_PROMPT_FILE = _SRC_ROOT / "config" / ".prompts.yaml"

# Literal ``"<bucket>/<path>"`` prompt references embedded in source. Every
# prompt path in this codebase is a static literal (no f-string path
# construction) EXCEPT ``f"template/{section.template_key}"`` in
# ``report.py``; those section keys are recovered from the imported
# ``_SYNTHESIS_SECTIONS`` struct below rather than this regex.
_PATH_LITERAL_RE = re.compile(r"""["'](system|template|user)/([\w./]+)["']""")

# ``{{> bucket/path }}`` include references embedded in the prompt YAML
# itself. ``user/_partials/*`` bodies are shared via these includes rather
# than loaded by any ``.py`` call site, so harvesting them keeps a partial
# "referenced" (live) while an unused partial still trips the inverse guard.
_INCLUDE_REF_RE = re.compile(r"\{\{>\s*([\w./]+)\s*\}\}")

# Leaves intentionally present without a code reference. ``deeploc`` and
# ``virtual_knockout`` are another contributor's staged-but-unwired prompts
# (foreign WIP -- do not delete); the rest load through the
# ``f"template/{...}"`` section path or nested-dict walks the literal
# sweep cannot see, and are pinned live by the forward checks above.
KNOWN_UNREFERENCED: frozenset[str] = frozenset(
    {
        # Foreign WIP: added but not yet wired into any agent.
        "user/deeploc",
        "user/deeploc_meta",
        "user/virtual_knockout",
        "user/virtual_knockout_meta",
        # Intended-but-unwired: no production code loads it, but its
        # Align-A citation quality is pinned by tests/agents/
        # test_prompt_align_a.py, so it is kept deliberately.
        "user/deep_research_report",
    }
)


def _load_yaml() -> dict:
    """Return the parsed tracked prompt file."""
    with open(_PROMPT_FILE, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _leaf_paths(node: object, prefix: tuple[str, ...] = ()) -> Iterable[str]:
    """Yield ``a/b/c`` paths for every leaf (non-dict) value."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_paths(value, prefix + (key,))
    else:
        yield "/".join(prefix)


def _referenced_paths() -> set[str]:
    """Collect every prompt path the code can load.

    Combines the literal ``"<bucket>/<path>"`` references in source with
    the imported map values, the report section structs, and the
    ``{{> bucket/path }}`` include references in the prompt YAML, so paths
    built via ``f"template/{template_key}"``, stored only in a dispatch
    map, or reachable only through a shared partial are all covered.
    """
    paths: set[str] = set()
    for py_file in _SRC_ROOT.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for bucket, rest in _PATH_LITERAL_RE.findall(text):
            paths.add(f"{bucket}/{rest}")
    paths.update(ANALYSIS_GOAL_TEMPLATE_MAP.values())
    paths.update(ANALYSIS_META_TEMPLATE_MAP.values())
    paths.update(
        f"template/{section.template_key}" for section in _SYNTHESIS_SECTIONS
    )
    yaml_text = _PROMPT_FILE.read_text(encoding="utf-8")
    paths.update(_INCLUDE_REF_RE.findall(yaml_text))
    return paths


def test_every_referenced_prompt_path_resolves_to_a_leaf_string() -> None:
    """Forward guard: code paths resolve to a leaf string, not a dict.

    A missing key raises ``KeyError`` inside ``load_template``; a key that
    resolves to a nested ``dict`` (e.g. the ``user/gene_expression_analysis``
    umbrella) returns a mapping that ``render_template`` then chokes on.
    Both are drift and both fail here.
    """
    data = _load_yaml()
    valid_leaves = set(_leaf_paths(data))
    broken: list[str] = []
    container: list[str] = []
    for path in sorted(_referenced_paths()):
        if path not in valid_leaves:
            broken.append(path)
            continue
        rendered = load_template(str(_PROMPT_FILE), path)
        if not isinstance(rendered, str):
            container.append(path)
    assert not broken, f"Code loads prompt keys absent from YAML: {broken}"
    assert not container, (
        f"Code loads prompt paths that resolve to a dict container, "
        f"not a leaf string: {container}"
    )


def test_no_unreferenced_prompt_leaves() -> None:
    """Inverse guard: every YAML leaf is referenced or explicitly allowed."""
    data = _load_yaml()
    referenced = _referenced_paths()
    dead = [
        leaf
        for leaf in _leaf_paths(data)
        if leaf not in referenced and leaf not in KNOWN_UNREFERENCED
    ]
    assert not dead, (
        f"Prompt leaves with no code reference (delete them or add to "
        f"KNOWN_UNREFERENCED if intentional): {sorted(dead)}"
    )
