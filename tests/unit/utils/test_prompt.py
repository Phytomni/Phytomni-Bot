# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for YAML prompt loading and rendering helpers.

Covers nested YAML lookups, top-level templates, missing path errors,
template rendering, prompt rendering, and cache invalidation by file changes.
"""

import pytest

from mcp_server_phytomni.common.prompts import (
    get_prompt,
    load_json_file,
    load_template,
    load_text_file,
    render_template,
)

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
