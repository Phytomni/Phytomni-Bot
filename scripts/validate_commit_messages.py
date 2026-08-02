#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Validate repository commit subjects and bullet-only bodies."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path

SUBJECT_PATTERN = re.compile(
    r"^(?P<emoji>\S+) (?P<prefix>[A-Z][A-Za-z0-9-]*): " + r"(?P<summary>\S.*)$"
)
TRAILER_PATTERN = re.compile(
    r"^(?:Co-authored-by|Signed-off-by|Reviewed-by|Acked-by|Tested-by|"
    + r"Fixes|Refs): .+$",
    re.IGNORECASE,
)
ZERO_OBJECT_ID = "0" * 40

Violation = tuple[int, str]


class CommitMessageGateError(RuntimeError):
    """Raised when the gate cannot resolve its requested input."""


def _clean_lines(message: str) -> list[str]:
    """Return the effective message lines after Git-style comment cleanup."""
    lines = [
        line
        for line in message.splitlines()
        if not line.lstrip().startswith("#")
    ]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def _subject_is_valid(subject: str) -> bool:
    """Return whether a subject matches the repository prefix convention."""
    match = SUBJECT_PATTERN.fullmatch(subject)
    if match is None:
        return False
    marker = match.group("emoji")
    return unicodedata.category(marker[0]).startswith("S")


def _body_end(lines: Sequence[str]) -> int:
    """Return the exclusive body end before a separated trailer block."""
    trailer_start = len(lines)
    while trailer_start > 2 and TRAILER_PATTERN.fullmatch(
        lines[trailer_start - 1]
    ):
        trailer_start -= 1
    if trailer_start < len(lines) and lines[trailer_start - 1] == "":
        return trailer_start - 1
    return len(lines)


def _trailing_whitespace_violations(lines: Sequence[str]) -> list[Violation]:
    """Return line-level trailing whitespace violations."""
    return [
        (line_number, "trailing whitespace is forbidden")
        for line_number, line in enumerate(lines, start=1)
        if line != line.rstrip()
    ]


def _body_violations(lines: Sequence[str]) -> list[Violation]:
    """Return violations for the required separated bullet body."""
    if len(lines) < 3 or lines[1] != "":
        return [(2, "a blank line and bullet body are required")]

    body_end = _body_end(lines)
    body = list(lines[2:body_end])
    while body and body[-1] == "":
        body.pop()
    if not body:
        return [(3, "a non-empty bullet body is required")]

    violations: list[Violation] = []
    previous_was_bullet = False
    for line_number, line in enumerate(body, start=3):
        if not line:
            previous_was_bullet = False
            continue
        if not line.startswith("- "):
            violations.append((line_number, "body lines must start with '- '"))
            previous_was_bullet = False
            continue
        if not line[2:].strip():
            violations.append((line_number, "bullet text is required"))
        if previous_was_bullet:
            violations.append(
                (
                    line_number,
                    "bullet paragraphs must be separated by a blank line",
                )
            )
        previous_was_bullet = True
    return violations


def validate_message(message: str) -> list[Violation]:
    """Return deterministic line-level style violations for one message."""
    lines = _clean_lines(message)
    if not lines:
        return [(1, "subject is required")]

    violations = _trailing_whitespace_violations(lines)

    if lines[0].startswith("Merge "):
        return violations

    if not _subject_is_valid(lines[0]):
        violations.append((1, "subject must match 'emoji + Word: summary'"))

    violations.extend(_body_violations(lines))
    return violations


def _git(*arguments: str) -> str:
    """Run Git and return stdout or raise a bounded gate error."""
    result = subprocess.run(
        ["git", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommitMessageGateError(
            f"git {' '.join(arguments[:2])} failed with exit "
            f"{result.returncode}"
        )
    return result.stdout


def _range_commits(revision_range: str) -> list[str]:
    """Resolve a revision range in oldest-first order."""
    return _git("rev-list", "--reverse", revision_range).splitlines()


def _unpublished_commits(tip: str) -> list[str]:
    """Resolve commits not reachable from any configured remote ref."""
    return _git(
        "rev-list",
        "--reverse",
        tip,
        "--not",
        "--remotes",
    ).splitlines()


def _github_event_commits(path: Path) -> list[str]:
    """Resolve the authored commit range represented by a GitHub event."""
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommitMessageGateError(
            "cannot read GitHub event payload"
        ) from exc
    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        try:
            base = pull_request["base"]["sha"]
            head = pull_request["head"]["sha"]
        except (KeyError, TypeError) as exc:
            raise CommitMessageGateError(
                "GitHub pull request payload has no base/head SHA"
            ) from exc
        return _range_commits(f"{base}..{head}")

    before = event.get("before")
    after = event.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        raise CommitMessageGateError(
            "GitHub event is not a push or pull request"
        )
    if after == ZERO_OBJECT_ID:
        return []
    if before == ZERO_OBJECT_ID:
        return [after]
    return _range_commits(f"{before}..{after}")


def _validate_commits(commits: Sequence[str]) -> list[str]:
    """Return formatted violations for the supplied commit objects."""
    failures: list[str] = []
    for commit in commits:
        message = _git("show", "--no-patch", "--format=%B", commit)
        for line_number, detail in validate_message(message):
            failures.append(f"{commit[:12]}: line {line_number}: {detail}")
    return failures


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--message-file", type=Path)
    source.add_argument("--range", dest="revision_range")
    source.add_argument("--not-on-remotes", metavar="TIP")
    source.add_argument("--github-event", type=Path)
    return parser


def _report_failures(failures: Sequence[str]) -> int:
    """Write bounded failures and return the gate exit status."""
    if not failures:
        return 0
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the commit-message gate."""
    options = _parser().parse_args(arguments)
    try:
        if options.message_file is not None:
            message = options.message_file.read_text(encoding="utf-8")
            failures = [
                f"commit message: line {line_number}: {detail}"
                for line_number, detail in validate_message(message)
            ]
            status = _report_failures(failures)
            if status == 0:
                print("Commit message style passed: 1 message")
            return status

        if options.revision_range is not None:
            commits = _range_commits(options.revision_range)
        elif options.not_on_remotes is not None:
            commits = _unpublished_commits(options.not_on_remotes)
        else:
            commits = _github_event_commits(options.github_event)
        status = _report_failures(_validate_commits(commits))
        if status == 0:
            noun = "commit" if len(commits) == 1 else "commits"
            print(f"Commit message style passed: {len(commits)} {noun}")
        return status
    except (CommitMessageGateError, OSError, UnicodeError) as exc:
        print(f"commit message gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
