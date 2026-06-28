# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Coverage guard binding every prompt placeholder to a real supplier.

The renderer blanks a missing parameter rather than raising, so a
placeholder no call site supplies degrades silently in production output.
This guard collects every directly-loaded prompt's placeholders and
asserts each is supplied by a known call site or a documented exception,
so a one-sided rename (``feed_back`` renamed in the YAML but not the
kwarg) or a stray new placeholder fails loudly instead of blanking.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

from mcp_server_phytomni.agents.deep_genome.report import _SYNTHESIS_SECTIONS
from mcp_server_phytomni.common.prompts import load_template

pytestmark = pytest.mark.unit

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "mcp_server_phytomni"
_PROMPT_FILE = _SRC_ROOT / "config" / ".prompts.yaml"
_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RENDER_FUNCS = frozenset({"get_prompt", "render_template"})

# Modules that pass a ``parameters`` dict built indirectly (a variable or a
# helper call), so the keys are not visible at the call expression and are
# harvested as module-level string constants instead.
_INDIRECT_SUPPLIER_MODULES = (
    "agents/review/summary.py",
    "agents/review/agent.py",
    "agents/deep_genome/report.py",
    "agents/brief_gene/introduction.py",
    "agents/brief_gene/analytical_sections.py",
)

# The named wiring this guard exists to lock; each must stay suppliable.
_NAMED_LOCKS = frozenset(
    {"feed_back", "raw_plan", "upload_context", "species_string"}
)

# Placeholders supplied through an f-string or subscript key the static
# harvest cannot see, but whose supplier is verified in production code.
_DYNAMIC_SUPPLIERS = frozenset(
    {
        # review/summary.py:68-69 builds f"subsection_{n}_{title,content}".
        "subsection_1_title",
        "subsection_1_content",
        "subsection_2_title",
        "subsection_2_content",
        "subsection_3_title",
        "subsection_3_content",
        "subsection_4_title",
        "subsection_4_content",
        # deep_genome/summary.py:200-218 assigns self.data["umap_path"] etc.
        "umap_path",
        "violin_path",
    }
)

# Placeholders intentionally left blank when absent. ``fst_path`` renders an
# image tag whose empty form ``![fst Image]()`` is stripped by the
# ``_EMPTY_IMAGE_RE`` pass in ``deep_genome/report.py``.
_INTENTIONALLY_OPTIONAL = frozenset({"fst_path"})

# Latent unsupplied placeholders surfaced by this guard would be pinned here
# (shrink-only) until their behavior fix lands. Currently none: the two it
# first found -- vci_analysis "year" and smep_analysis "epic_type", both of
# which rendered blank -- were resolved by dropping the placeholders.
_KNOWN_UNSUPPLIED_GAPS: frozenset[str] = frozenset()

# Prompts with no production loader (pinned only by an offline fixture), so
# their placeholders are supplied by a test, not a call site. Mirrors
# ``test_prompt_drift.KNOWN_UNREFERENCED``.
_UNWIRED_PROMPTS = frozenset({"user/deep_research_report"})


def _iter_leaf_paths(node, trail=()):
    """Yield ``a/b/c`` for each non-dict leaf in the parsed YAML tree."""
    if not isinstance(node, dict):
        yield "/".join(trail)
        return
    for name, child in node.items():
        yield from _iter_leaf_paths(child, trail + (name,))


def _inline_parameter_keys() -> set[str]:
    """Harvest string-literal keys of every inline ``parameters`` dict."""
    found: set[str] = set()
    for module in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            label = getattr(func, "attr", None) or getattr(func, "id", None)
            if label not in _RENDER_FUNCS:
                continue
            params = None
            if label == "get_prompt" and len(call.args) >= 3:
                params = call.args[2]
            elif label == "render_template" and len(call.args) >= 2:
                params = call.args[1]
            for keyword in call.keywords:
                if keyword.arg == "parameters":
                    params = keyword.value
            if not isinstance(params, ast.Dict):
                continue
            for entry in params.keys:
                if isinstance(entry, ast.Constant) and isinstance(
                    entry.value, str
                ):
                    found.add(entry.value)
    return found


def _indirect_supplier_identifiers() -> set[str]:
    """Harvest identifier-like string constants from indirect suppliers."""
    found: set[str] = set()
    for relative in _INDIRECT_SUPPLIER_MODULES:
        tree = ast.parse((_SRC_ROOT / relative).read_text(encoding="utf-8"))
        for literal in ast.walk(tree):
            if isinstance(literal, ast.Constant) and isinstance(
                literal.value, str
            ):
                if _IDENTIFIER_RE.match(literal.value):
                    found.add(literal.value)
    return found


def _synthesis_section_keys() -> set[str]:
    """Return the report fragment data keys plus ``section_number``."""
    keys = {"section_number"}
    for section in _SYNTHESIS_SECTIONS:
        keys.update(section.required_keys)
        keys.update(section.legend_keys)
    return keys


def _supplier_universe() -> set[str]:
    """Union every placeholder a production call site can supply.

    Three sources: (1) string-literal keys of every ``parameters`` dict
    passed inline to ``get_prompt`` / ``render_template``; (2) every
    identifier-like string constant in the five modules that build a
    ``parameters`` dict indirectly (a local variable or a helper return),
    whose keys never appear at the call expression; (3)
    ``_SYNTHESIS_SECTIONS`` data keys plus ``section_number``, the report
    fragment placeholders ``deep_genome/report.py`` supplies.
    """
    return (
        _inline_parameter_keys()
        | _indirect_supplier_identifiers()
        | _synthesis_section_keys()
    )


def _placeholders_by_prompt() -> dict[str, set[str]]:
    """Map each directly-loaded prompt path to its placeholder names."""
    data = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))
    mapping: dict[str, set[str]] = {}
    for path in _iter_leaf_paths(data):
        if "/_partials/" in path or path in _UNWIRED_PROMPTS:
            continue
        body = load_template(str(_PROMPT_FILE), path)
        names = {
            hit.group(1).strip()
            for hit in _PLACEHOLDER_RE.finditer(body)
            if hit.group(1).strip()[:1] not in ("#", "/", ">")
        }
        if names:
            mapping[path] = names
    return mapping


def test_named_placeholder_wiring_stays_suppliable() -> None:
    """The renamed-prone placeholders must stay backed by a call site."""
    universe = _supplier_universe()
    missing = sorted(_NAMED_LOCKS - universe)
    assert not missing, (
        "Named placeholder wiring lost its supplier (a one-sided rename?): "
        f"{missing}"
    )


def test_every_placeholder_is_supplied_or_documented() -> None:
    """Every prompt placeholder is supplied or a documented exception."""
    universe = _supplier_universe()
    documented = (
        _DYNAMIC_SUPPLIERS | _INTENTIONALLY_OPTIONAL | _KNOWN_UNSUPPLIED_GAPS
    )
    offenders: dict[str, list[str]] = {}
    for path, names in _placeholders_by_prompt().items():
        stray = sorted(name for name in names if name not in universe)
        stray = [name for name in stray if name not in documented]
        if stray:
            offenders[path] = stray
    assert not offenders, (
        "Prompt placeholders with no supplier and no documented exception "
        f"(wire them at the call site or document them): {offenders}"
    )
