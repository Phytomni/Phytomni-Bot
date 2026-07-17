# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Collect exact static-analysis suppressions from tool configuration."""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

from ..model import Finding, Mechanism, TargetKind
from .helpers import FindingParts, make_finding


class _ConfigEntry(TypedDict):
    """One configuration key/value that creates a finding."""

    tool: str
    rule: str
    key: str
    value: object


def _relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _rule_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        )
    return ()


def _config_finding(root: Path, path: Path, entry: _ConfigEntry) -> Finding:
    display_path = _relative_path(root, path)
    source = f"{entry['key']}={entry['value']!r}"
    parts: FindingParts = {
        "tool": entry["tool"],
        "rule": entry["rule"],
        "mechanism": Mechanism.CONFIG,
        "target_kind": TargetKind.CONFIG,
        "path": display_path,
        "symbol": entry["key"],
        "peer_path": None,
        "peer_symbol": None,
        "location": f"{display_path}:{entry['key']}",
        "message": source,
        "source": source,
    }
    return make_finding(parts)


def _entry(tool: str, rule: str, key: str, value: object) -> _ConfigEntry:
    return {
        "tool": tool,
        "rule": rule,
        "key": key,
        "value": value,
    }


def _collect_ruff(
    root: Path, path: Path, config: dict[str, Any]
) -> Iterable[Finding]:
    lint = config.get("tool", {}).get("ruff", {}).get("lint", {})
    if not isinstance(lint, dict):
        return ()
    findings: list[Finding] = []
    for key in ("ignore", "extend-ignore"):
        for rule in _rule_values(lint.get(key)):
            findings.append(
                _config_finding(
                    root,
                    path,
                    _entry("ruff", rule, f"tool.ruff.lint.{key}", lint[key]),
                )
            )
    per_file = lint.get("per-file-ignores", {})
    if isinstance(per_file, dict):
        for pattern, values in sorted(per_file.items()):
            for rule in _rule_values(values):
                key = f"tool.ruff.lint.per-file-ignores.{pattern}"
                findings.append(
                    _config_finding(
                        root, path, _entry("ruff", rule, key, values)
                    )
                )
    return findings


def _collect_pylint(
    root: Path, path: Path, config: dict[str, Any]
) -> Iterable[Finding]:
    pylint = config.get("tool", {}).get("pylint", {})
    if not isinstance(pylint, dict):
        return ()
    findings: list[Finding] = []
    main = pylint.get("main", {})
    if isinstance(main, dict):
        for ignored in _rule_values(main.get("ignore")):
            findings.append(
                _config_finding(
                    root,
                    path,
                    _entry(
                        "pylint",
                        "path-ignore",
                        "tool.pylint.main.ignore",
                        ignored,
                    ),
                )
            )
    design = pylint.get("design", {})
    if isinstance(design, dict) and "max-module-lines" in design:
        findings.append(
            _config_finding(
                root,
                path,
                _entry(
                    "pylint",
                    "max-module-lines",
                    "tool.pylint.design.max-module-lines",
                    design["max-module-lines"],
                ),
            )
        )
    formatting = pylint.get("format", {})
    if isinstance(formatting, dict) and "max-module-lines" in formatting:
        findings.append(
            _config_finding(
                root,
                path,
                _entry(
                    "pylint",
                    "max-module-lines",
                    "tool.pylint.format.max-module-lines",
                    formatting["max-module-lines"],
                ),
            )
        )
    control = pylint.get("messages control", {})
    if isinstance(control, dict):
        for rule in _rule_values(control.get("disable")):
            findings.append(
                _config_finding(
                    root,
                    path,
                    _entry(
                        "pylint",
                        rule,
                        'tool.pylint."messages control".disable',
                        control["disable"],
                    ),
                )
            )
    return findings


def _collect_pymarkdown(
    root: Path, path: Path, config: dict[str, Any]
) -> Iterable[Finding]:
    pymarkdown = config.get("tool", {}).get("pymarkdown", {})
    if not isinstance(pymarkdown, dict):
        return ()
    plugins = pymarkdown.get("plugins", {})
    if not isinstance(plugins, dict):
        return ()
    findings: list[Finding] = []
    for rule, options in sorted(plugins.items()):
        if not isinstance(options, dict):
            continue
        if options.get("enabled") is False:
            key = f"tool.pymarkdown.plugins.{rule}.enabled"
            findings.append(
                _config_finding(
                    root, path, _entry("pymarkdown", rule, key, False)
                )
            )
    return findings


def _collect_mypy(
    root: Path, path: Path, config: dict[str, Any]
) -> Iterable[Finding]:
    overrides = config.get("tool", {}).get("mypy", {}).get("overrides", [])
    if not isinstance(overrides, list):
        return ()
    findings: list[Finding] = []
    for index, override in enumerate(overrides):
        if not isinstance(override, dict):
            continue
        prefix = f"tool.mypy.overrides[{index}]"
        for key, value in sorted(override.items()):
            if key in {
                "ignore_missing_imports",
                "ignore_errors",
                "disable_error_code",
            }:
                for rule in _rule_values(value) or (key,):
                    findings.append(
                        _config_finding(
                            root,
                            path,
                            _entry("mypy", rule, f"{prefix}.{key}", value),
                        )
                    )
    return findings


def _collect_flake8(root: Path) -> Iterable[Finding]:
    findings: list[Finding] = []
    for name in (".flake8", "setup.cfg", "tox.ini"):
        path = root / name
        if not path.exists():
            continue
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")
        for section in parser.sections():
            if not section.lower().startswith("flake8"):
                continue
            for key in ("ignore", "extend-ignore"):
                if not parser.has_option(section, key):
                    continue
                raw = parser.get(section, key)
                for rule in _rule_values(raw):
                    findings.append(
                        _config_finding(
                            root,
                            path,
                            _entry("flake8", rule, f"[{section}].{key}", raw),
                        )
                    )
    return findings


def collect_config_suppressions(root: Path) -> tuple[Finding, ...]:
    """Collect exact ignore/disable settings from supported config files."""
    findings: list[Finding] = []
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        with pyproject.open("rb") as handle:
            config = tomllib.load(handle)
        if isinstance(config, dict):
            findings.extend(_collect_ruff(root, pyproject, config))
            findings.extend(_collect_pylint(root, pyproject, config))
            findings.extend(_collect_pymarkdown(root, pyproject, config))
            findings.extend(_collect_mypy(root, pyproject, config))
    findings.extend(_collect_flake8(root))
    return tuple(
        sorted(
            findings,
            key=lambda item: (item.path, item.symbol or "", item.rule),
        )
    )
