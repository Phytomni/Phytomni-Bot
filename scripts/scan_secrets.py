#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Scan tracked, staged, or changed Git content for likely secrets."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern

MAX_FILE_BYTES = 1_000_000
ALLOWLIST_MARKERS = (
    "pragma: allowlist secret",
    "pragma: allowlist-secret",
    "nosec",
)
PLACEHOLDER_WORDS = (
    "changeme",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "sample",
    "test",
    "your",
)
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "htmlcov",
}
ALLOWED_ENV_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
}
SENSITIVE_FILE_NAMES = {
    ".npmrc",
    ".pypirc",
    "client_secret.json",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
}
SENSITIVE_SUFFIXES = {
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}


@dataclass(frozen=True)
class Rule:
    """A regular expression rule for a sensitive value."""

    name: str
    pattern: Pattern[str]
    message: str


@dataclass(frozen=True)
class Finding:
    """A single secret-scan finding."""

    source: str
    path: str
    line_number: int
    rule: str
    message: str
    context: str

    @property
    def location(self) -> str:
        """Return the source location for display."""
        if self.line_number > 0:
            return f"{self.path}:{self.line_number}"
        return self.path


SECRET_RULES = (
    Rule(
        name="private-key",
        pattern=re.compile(
            r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
        ),
        message="private key material must not be committed",
    ),
    Rule(
        name="github-token",
        pattern=re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
        message="GitHub token-like value detected",
    ),
    Rule(
        name="openai-token",
        pattern=re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        message="OpenAI token-like value detected",
    ),
    Rule(
        name="aws-access-key",
        pattern=re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|ASIA)[A-Z0-9]{16}\b"),
        message="cloud access key-like value detected",
    ),
    Rule(
        name="jwt-token",
        pattern=re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\."
            r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        message="JWT-like token detected",
    ),
    Rule(
        name="bearer-token",
        pattern=re.compile(
            r"(?i)\bBearer\s+(?P<value>[A-Za-z0-9._~+/=-]{24,})"
        ),
        message="Bearer token-like value detected",
    ),
    Rule(
        name="secret-assignment",
        pattern=re.compile(
            r"(?ix)"
            r"\b(?:"
            r"access[_-]?key|api[_-]?key|auth[_-]?token|"
            r"client[_-]?secret|password|passwd|private[_-]?token|"
            r"refresh[_-]?token|secret[_-]?key|x[-_]?auth[-_]?token"
            r")\b"
            r"\s*[:=]\s*"
            r"['\"]?"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_+/=-]{15,})"
            r"(?=$|[\s,'\"#])"
        ),
        message="non-placeholder secret assignment detected",
    ),
)


def is_placeholder(value: str) -> bool:
    """Return whether a matched value is an obvious placeholder."""
    normalized = value.strip(" '\"\t\r\n").lower()
    if not normalized:
        return True
    if len(set(normalized)) <= 2:
        return True
    return any(word in normalized for word in PLACEHOLDER_WORDS)


def should_skip_path(path: str) -> bool:
    """Return whether a path should be skipped during content scans."""
    return any(part in SKIP_PARTS for part in Path(path).parts)


def sensitive_path_reason(path: str) -> str | None:
    """Return a reason when a tracked path itself looks sensitive."""
    file_path = Path(path)
    name = file_path.name.lower()
    if name in ALLOWED_ENV_NAMES:
        return None
    if name == ".env" or name.startswith(".env."):
        return "environment files must stay out of Git history"
    if name in SENSITIVE_FILE_NAMES:
        return "credential file names must stay out of Git history"
    if file_path.suffix.lower() in SENSITIVE_SUFFIXES:
        return "private key or certificate files must stay out of Git history"
    return None


def redact_line(line: str, match: re.Match[str]) -> str:
    """Return a display-safe copy of a matched line."""
    value = match.groupdict().get("value")
    redacted = line.strip()
    if value:
        redacted = redacted.replace(value, "<redacted>")
    else:
        redacted = redacted.replace(match.group(0), "<redacted>")
    return redacted[:160]


def scan_line(
    source: str, path: str, line_number: int, line: str
) -> list[Finding]:
    """Scan a single line and return findings."""
    lowered = line.lower()
    if any(marker in lowered for marker in ALLOWLIST_MARKERS):
        return []

    findings: list[Finding] = []
    for rule in SECRET_RULES:
        for match in rule.pattern.finditer(line):
            value = match.groupdict().get("value", match.group(0))
            if is_placeholder(value):
                continue
            findings.append(
                Finding(
                    source=source,
                    path=path,
                    line_number=line_number,
                    rule=rule.name,
                    message=rule.message,
                    context=redact_line(line, match),
                )
            )
    return findings


def scan_text(source: str, path: str, text: str) -> list[Finding]:
    """Scan text content for likely secrets."""
    findings: list[Finding] = []
    reason = sensitive_path_reason(path)
    if reason:
        findings.append(
            Finding(
                source=source,
                path=path,
                line_number=0,
                rule="sensitive-path",
                message=reason,
                context="<path>",
            )
        )

    for line_number, line in enumerate(text.splitlines(), start=1):
        findings.extend(scan_line(source, path, line_number, line))
    return findings


def decode_bytes(raw_content: bytes) -> str | None:
    """Decode file content as text, returning None for binary data."""
    if b"\0" in raw_content:
        return None
    try:
        return raw_content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def run_git(
    args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a Git command and return the completed process."""
    return subprocess.run(
        ["git", *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def tracked_files() -> list[str]:
    """Return tracked files in the current Git checkout."""
    result = run_git(["ls-files", "-z"])
    return [path for path in result.stdout.split("\0") if path]


def staged_files() -> list[str]:
    """Return added, copied, modified, or renamed staged files."""
    result = run_git(
        [
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
        ]
    )
    return [path for path in result.stdout.split("\0") if path]


def scan_worktree_path(path: str) -> list[Finding]:
    """Scan a tracked path from the working tree."""
    if should_skip_path(path):
        return []
    file_path = Path(path)
    reason = sensitive_path_reason(path)
    if reason and not file_path.exists():
        return [
            Finding("tracked", path, 0, "sensitive-path", reason, "<path>")
        ]
    if not file_path.is_file() or file_path.stat().st_size > MAX_FILE_BYTES:
        return []
    raw_content = file_path.read_bytes()
    text = decode_bytes(raw_content)
    if text is None:
        return []
    return scan_text("tracked", path, text)


def scan_staged_path(path: str) -> list[Finding]:
    """Scan a staged path from the Git index."""
    if should_skip_path(path):
        return []
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0 or len(result.stdout) > MAX_FILE_BYTES:
        return []
    text = decode_bytes(result.stdout)
    if text is None:
        return []
    return scan_text("staged", path, text)


def scan_paths(paths: list[str], *, staged: bool) -> list[Finding]:
    """Scan a list of paths from the working tree or Git index."""
    findings: list[Finding] = []
    for path in paths:
        if staged:
            findings.extend(scan_staged_path(path))
        else:
            findings.extend(scan_worktree_path(path))
    return findings


def scan_range_paths(range_spec: str) -> list[Finding]:
    """Scan path names changed in a Git revision range."""
    result = run_git(["log", "--name-only", "--format=", range_spec])
    findings: list[Finding] = []
    seen: set[str] = set()
    for path in result.stdout.splitlines():
        if not path or path in seen or should_skip_path(path):
            continue
        seen.add(path)
        reason = sensitive_path_reason(path)
        if reason:
            findings.append(
                Finding(
                    "git-range", path, 0, "sensitive-path", reason, "<path>"
                )
            )
    return findings


def parse_hunk_start(line: str) -> int | None:
    """Parse a unified-diff hunk header and return the new-file start line."""
    match = re.search(r"\+(\d+)(?:,\d+)?", line)
    if not match:
        return None
    return int(match.group(1))


def parse_diff_path(line: str) -> str:
    """Parse the new path from a diff header."""
    parts = line.split()
    if len(parts) < 4:
        return "<unknown>"
    path = parts[3]
    if path.startswith("b/"):
        path = path[2:]
    return path


def scan_range_patch(range_spec: str) -> list[Finding]:
    """Scan added lines in a Git revision range."""
    result = run_git(
        [
            "log",
            "--format=commit %H",
            "--patch",
            "--no-ext-diff",
            range_spec,
        ]
    )
    findings: list[Finding] = []
    commit = "<unknown>"
    path = "<unknown>"
    line_number = 0

    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("commit "):
            commit = raw_line.split(maxsplit=1)[1][:12]
            continue
        if raw_line.startswith("diff --git "):
            path = parse_diff_path(raw_line)
            line_number = 0
            continue
        if raw_line.startswith("@@ "):
            line_number = parse_hunk_start(raw_line) or 0
            continue
        if raw_line.startswith("+++") or raw_line.startswith("---"):
            continue
        if raw_line.startswith("+"):
            added_line = raw_line[1:]
            source = f"git-range:{commit}"
            findings.extend(scan_line(source, path, line_number, added_line))
            if line_number:
                line_number += 1
            continue
        if raw_line.startswith("-"):
            continue
        if line_number:
            line_number += 1

    return findings


def scan_git_range(range_spec: str) -> list[Finding]:
    """Scan changed paths and added lines in a Git revision range."""
    return scan_range_paths(range_spec) + scan_range_patch(range_spec)


def print_findings(findings: list[Finding]) -> None:
    """Print findings in a compact form."""
    print(
        f"Secret scan failed with {len(findings)} finding(s):", file=sys.stderr
    )
    for finding in findings:
        print(
            f"- {finding.location} [{finding.source}] "
            f"{finding.rule}: {finding.message}",
            file=sys.stderr,
        )
        print(f"  context: {finding.context}", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        action="store_true",
        help="scan all tracked files in the working tree",
    )
    mode.add_argument(
        "--staged",
        action="store_true",
        help="scan staged files from the Git index",
    )
    mode.add_argument(
        "--git-range",
        metavar="RANGE",
        help="scan added lines and changed paths in a Git revision range",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the secret scanner."""
    args = parse_args(argv or sys.argv[1:])
    if args.staged:
        findings = scan_paths(staged_files(), staged=True)
    elif args.git_range:
        findings = scan_git_range(args.git_range)
    else:
        findings = scan_paths(tracked_files(), staged=False)

    if findings:
        print_findings(findings)
        return 1

    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
