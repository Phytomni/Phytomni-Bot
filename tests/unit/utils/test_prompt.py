# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for YAML prompt loading and rendering helpers.

Covers nested YAML lookups, top-level templates, missing path errors,
template rendering, prompt rendering, and cache invalidation by file changes.
"""

import re
from pathlib import Path

import pytest
import yaml

from mcp_server_phytomni.common.prompts import (
    get_prompt,
    load_json_file,
    load_template,
    load_text_file,
    render_template,
)

_PROMPT_FILE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "mcp_server_phytomni"
    / "config"
    / ".prompts.yaml"
)
_VAR_RE = re.compile(r"\{\{([^}]+)\}\}")
_NEW_MARKERS = ("{{#", "{{>", "{{/")

pytestmark = pytest.mark.unit


def test_load_template_reads_nested_yaml_path(tmp_path):
    """Verify load_template reads nested YAML path.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "system:\n  greeting: 'Hello {{ name }}'\n",
        encoding="utf-8",
    )

    assert load_template(str(template_file), "system/greeting") == (
        "Hello {{ name }}"
    )


def test_load_template_reads_single_top_level_value(tmp_path):
    """Verify load_template reads single top-level value.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text("only: plain text\n", encoding="utf-8")

    assert load_template(str(template_file)) == "plain text"


def test_load_template_raises_for_missing_nested_path(tmp_path):
    """Verify load_template raises for missing nested path.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text("system:\n  greeting: hello\n", encoding="utf-8")

    with pytest.raises(KeyError, match="Path 'missing' not found"):
        load_template(str(template_file), "system/missing")


def test_render_template_stringifies_values_and_warns_on_missing_key():
    """Verify render template stringifies values and warns on missing key."""
    with pytest.warns(UserWarning, match="Missing parameter 'missing'"):
        rendered = render_template(
            "Hello {{ name }} {{ count }} {{ missing }}",
            {"name": "leaf", "count": 3},
        )

    assert rendered == "Hello leaf 3 "


def test_get_prompt_loads_and_renders_template(tmp_path):
    """Verify get_prompt loads and renders template.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n  database: 'Query: {{ user_query }}'\n",
        encoding="utf-8",
    )

    prompt = get_prompt(
        str(template_file),
        "user/database",
        {"user_query": "plant height"},
    )

    assert prompt == "Query: plant height"


def test_load_template_cache_key_tracks_file_changes(tmp_path):
    """Verify load_template cache key tracks file changes.

    Args:
        tmp_path: Temporary directory for mutable prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text("only: first\n", encoding="utf-8")

    assert load_template(str(template_file)) == "first"

    template_file.write_text("only: second value\n", encoding="utf-8")

    assert load_template(str(template_file)) == "second value"


def test_load_json_file_cache_key_tracks_file_changes(tmp_path):
    """Verify load_json_file cache key tracks file changes.

    Args:
        tmp_path: Temporary directory for mutable JSON.
    """
    json_file = tmp_path / "data.json"
    json_file.write_text('{"value": 1}', encoding="utf-8")

    assert load_json_file(str(json_file)) == {"value": 1}

    json_file.write_text('{"value": 2, "label": "leaf"}', encoding="utf-8")

    assert load_json_file(str(json_file)) == {
        "value": 2,
        "label": "leaf",
    }


def test_load_text_file_cache_key_tracks_file_changes(tmp_path):
    """Verify load_text_file cache key tracks file changes.

    Args:
        tmp_path: Temporary directory for mutable text.
    """
    text_file = tmp_path / "region.json"
    text_file.write_text('{"region": "old"}', encoding="utf-8")

    assert load_text_file(str(text_file)) == '{"region": "old"}'

    text_file.write_text(
        '{"region": "new", "code": "510000"}', encoding="utf-8"
    )

    assert load_text_file(str(text_file)) == (
        '{"region": "new", "code": "510000"}'
    )


def test_render_template_keeps_if_block_when_param_non_empty():
    """Verify a truthy {{#if}} keeps its block body."""
    assert render_template("A{{#if x}}B{{/if}}C", {"x": "v"}) == "ABC"


def test_render_template_drops_if_block_when_param_empty_or_absent():
    """Verify an empty or absent {{#if}} param drops the whole block."""
    assert render_template("A{{#if x}}B{{/if}}C", {"x": ""}) == "AC"
    assert render_template("A{{#if x}}B{{/if}}C", {}) == "AC"


def test_render_template_unless_block_inverts_truthiness():
    """Verify {{#unless}} keeps its block only when the param is falsy."""
    assert render_template("A{{#unless x}}B{{/unless}}C", {"x": ""}) == "ABC"
    assert render_template("A{{#unless x}}B{{/unless}}C", {"x": "v"}) == "AC"


def test_render_template_nested_conditionals_resolve_independently():
    """Verify nested {{#if}} blocks evaluate by their own params."""
    template = "{{#if a}}A{{#if b}}B{{/if}}{{/if}}"
    assert render_template(template, {"a": "1", "b": "1"}) == "AB"
    assert render_template(template, {"a": "1", "b": ""}) == "A"
    assert render_template(template, {"a": "", "b": "1"}) == ""


def test_render_template_substitutes_vars_inside_kept_conditional():
    """Verify {{var}} substitution still runs inside a kept block."""
    template = "{{#if up}}files: {{up}}{{/if}}"
    assert render_template(template, {"up": "x.pdf"}) == "files: x.pdf"
    assert render_template(template, {"up": ""}) == ""


def test_render_template_unbalanced_conditional_raises():
    """Verify a stray close or an unclosed block raises ValueError."""
    with pytest.raises(ValueError):
        render_template("A{{/if}}", {})
    with pytest.raises(ValueError):
        render_template("{{#if x}}A", {"x": "v"})


def test_render_template_mismatched_conditional_kind_raises():
    """Verify an {{#if}} closed by {{/unless}} raises ValueError."""
    with pytest.raises(ValueError):
        render_template("{{#if x}}A{{/unless}}", {"x": "v"})


def test_render_template_padded_stray_close_raises():
    """Verify a whitespace-padded stray close raises like the bare form.

    Without symmetric whitespace tolerance ``{{/if }}`` would slip past the
    conditional scanner and be silently blanked by the flat-var pass instead
    of failing loudly as a stray close marker.
    """
    with pytest.raises(ValueError):
        render_template("A{{/if }}C", {})


def test_render_template_tolerates_whitespace_in_conditional_markers():
    """Verify padded open and close markers pair symmetrically.

    The open marker and the include marker both absorb trailing whitespace,
    so the close marker must too; otherwise a visually balanced block fails
    with a misleading 'Unclosed' error.
    """
    template = "A{{#if x }}B{{/if }}C"
    assert render_template(template, {"x": "v"}) == "ABC"
    assert render_template(template, {"x": ""}) == "AC"


def test_load_template_expands_include(tmp_path):
    """Verify {{> path}} is replaced by the referenced partial body.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n"
        "  shared: 'common body'\n"
        "  host: 'pre {{> user/shared }} post'\n",
        encoding="utf-8",
    )

    assert load_template(str(template_file), "user/host") == (
        "pre common body post"
    )


def test_load_template_include_recurses(tmp_path):
    """Verify an include whose body includes another partial recurses.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n"
        "  leaf: 'L'\n"
        "  mid: 'm{{> user/leaf }}m'\n"
        "  top: 't{{> user/mid }}t'\n",
        encoding="utf-8",
    )

    assert load_template(str(template_file), "user/top") == "tmLmt"


def test_load_template_include_cycle_raises_value_error(tmp_path):
    """Verify a mutual include cycle raises ValueError, not recursion.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n  a: 'a{{> user/b }}'\n  b: 'b{{> user/a }}'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="[Cc]ycl"):
        load_template(str(template_file), "user/a")


def test_load_template_include_missing_partial_raises_keyerror(tmp_path):
    """Verify an include of an absent path raises KeyError.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n  host: '{{> user/nope }}'\n",
        encoding="utf-8",
    )

    with pytest.raises(KeyError):
        load_template(str(template_file), "user/host")


def test_load_template_include_to_container_raises(tmp_path):
    """Verify an include resolving to a dict (not text) raises ValueError.

    Args:
        tmp_path: Temporary directory for prompt YAML.
    """
    template_file = tmp_path / "prompts.yaml"
    template_file.write_text(
        "user:\n"
        "  group:\n"
        "    inner: 'x'\n"
        "  host: '{{> user/group }}'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_template(str(template_file), "user/host")


def _var_only_render(template, parameters):
    """Reference pre-P2 renderer: the {{var}} pass alone, blank on missing."""

    def replacer(match):
        name = match.group(1).strip()
        return str(parameters[name]) if name in parameters else ""

    return _VAR_RE.sub(replacer, template)


def _leaf_items(node, prefix=()):
    """Yield (path, leaf-text) for every non-dict leaf in the YAML tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaf_items(value, prefix + (key,))
    else:
        yield "/".join(prefix), node


def test_renderer_is_byte_identical_to_var_only_for_marker_free_prompts():
    """Verify the new layers are a no-op on every marker-free real prompt.

    Loads each leaf of the tracked ``.prompts.yaml`` and asserts both that
    ``load_template`` returns it verbatim (no include rewrite) and that
    ``render_template`` matches the var-only reference, so activating
    includes/conditionals cannot change any existing rendered output.
    """
    data = yaml.safe_load(_PROMPT_FILE.read_text(encoding="utf-8"))
    checked = 0
    for path, leaf in _leaf_items(data):
        if any(marker in leaf for marker in _NEW_MARKERS):
            continue
        names = {match.group(1).strip() for match in _VAR_RE.finditer(leaf)}
        params = {name: f"<{name}>" for name in names}
        assert load_template(str(_PROMPT_FILE), path) == leaf
        assert render_template(leaf, params) == _var_only_render(leaf, params)
        checked += 1
    assert checked >= 80
