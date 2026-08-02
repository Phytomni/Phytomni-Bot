# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Behavior tests for the repository commit-message style gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "validate_commit_messages.py"
HOOK = ROOT / ".githooks" / "commit-msg"
VALID_MESSAGE = """\
📄 Contracts: Enforce commit message style

- Reject prose-only commit bodies.

- Check local and pushed commit ranges.

- Tests: exercise files, revisions, and hooks.
"""


def _run_checker(
    *arguments: str,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    """Run the real checker with captured output."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_message(tmp_path: Path, text: str) -> Path:
    """Write one controlled commit message fixture."""
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8")
    return path


def _git(repository: Path, *arguments: str) -> str:
    """Run a Git command in a controlled temporary repository."""
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _init_repository(path: Path) -> None:
    """Initialize a repository with local test identity."""
    path.mkdir()
    _git(path, "init", "--quiet")
    _git(path, "config", "user.name", "Commit Gate Test")
    _git(path, "config", "user.email", "commit-gate@example.invalid")


def _commit_message(repository: Path, tmp_path: Path, text: str) -> str:
    """Create an empty commit and return its full object id."""
    message = _write_message(tmp_path, text)
    _git(
        repository,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--quiet",
        "--allow-empty",
        "--file",
        str(message),
    )
    return _git(repository, "rev-parse", "HEAD")


def test_message_file_accepts_repository_style(tmp_path: Path) -> None:
    """A repository-style subject and bullet body pass unchanged."""
    message = _write_message(tmp_path, VALID_MESSAGE)

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 0
    assert result.stdout == "Commit message style passed: 1 message\n"
    assert result.stderr == ""


def test_message_file_rejects_prose_body(tmp_path: Path) -> None:
    """A prose paragraph cannot replace a bullet body."""
    message = _write_message(
        tmp_path,
        "📄 Contracts: Enforce commit message style\n\n"
        "Add a checker without the repository bullet marker.\n",
    )

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "commit message: line 3: body lines must start with '- '\n"
    )


def test_message_file_requires_a_body(tmp_path: Path) -> None:
    """A subject-only commit cannot omit its public change detail."""
    message = _write_message(
        tmp_path,
        "📄 Contracts: Enforce commit message style\n",
    )

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 1
    assert result.stderr == (
        "commit message: line 2: a blank line and bullet body are required\n"
    )


def test_message_file_rejects_adjacent_bullets(tmp_path: Path) -> None:
    """Each bullet paragraph must retain its separating blank line."""
    message = _write_message(
        tmp_path,
        "📄 Contracts: Enforce commit message style\n\n"
        "- Add the local hook.\n"
        "- Add the range check.\n",
    )

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 1
    assert result.stderr == (
        "commit message: line 4: bullet paragraphs must be separated "
        "by a blank line\n"
    )


def test_message_file_rejects_subject_without_emoji(tmp_path: Path) -> None:
    """The subject must retain the repository emoji and English prefix."""
    message = _write_message(
        tmp_path,
        "Contracts: Enforce commit message style\n\n"
        "- Add the local hook.\n",
    )

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 1
    assert result.stderr == (
        "commit message: line 1: subject must match "
        "'emoji + Word: summary'\n"
    )


def test_message_file_allows_standard_git_trailers(tmp_path: Path) -> None:
    """Standard attribution trailers remain outside the bullet body."""
    message = _write_message(
        tmp_path,
        VALID_MESSAGE
        + "\nCo-authored-by: Test Author <test-author@example.invalid>\n",
    )

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 0
    assert result.stderr == ""


def test_message_file_allows_generated_merge_message(tmp_path: Path) -> None:
    """An explicitly authorized Git merge can retain its generated message."""
    message = _write_message(tmp_path, "Merge branch 'topic'\n")

    result = _run_checker("--message-file", str(message))

    assert result.returncode == 0
    assert result.stderr == ""


def test_range_reports_the_offending_commit(tmp_path: Path) -> None:
    """Range mode identifies the exact commit and invalid body line."""
    repository = tmp_path / "repository"
    _init_repository(repository)
    base = _commit_message(repository, tmp_path, VALID_MESSAGE)
    bad = _commit_message(
        repository,
        tmp_path,
        "🐛 Runtime: Reject drifting commit bodies\n\n"
        "This paragraph lost its bullet marker.\n",
    )

    result = _run_checker("--range", f"{base}..HEAD", cwd=repository)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"{bad[:12]}: line 3: body lines must start with '- '\n"
    )


def test_not_on_remotes_checks_only_unpublished_commits(
    tmp_path: Path,
) -> None:
    """Local range mode excludes commits already reachable by a remote."""
    repository = tmp_path / "repository"
    remote = tmp_path / "remote.git"
    _init_repository(repository)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _commit_message(repository, tmp_path, VALID_MESSAGE)
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "--quiet", "--set-upstream", "origin", "HEAD")
    bad = _commit_message(
        repository,
        tmp_path,
        "🧪 Tests: Detect unpublished message drift\n\n"
        "This unpublished body is not a bullet.\n",
    )

    result = _run_checker("--not-on-remotes", "HEAD", cwd=repository)

    assert result.returncode == 1
    assert result.stderr.startswith(f"{bad[:12]}: line 3:")


def test_github_event_checks_pull_request_head_range(tmp_path: Path) -> None:
    """GitHub event mode validates the PR head rather than a merge commit."""
    repository = tmp_path / "repository"
    _init_repository(repository)
    base = _commit_message(repository, tmp_path, VALID_MESSAGE)
    bad = _commit_message(
        repository,
        tmp_path,
        "📄 Contracts: Check pull request messages\n\n"
        "The pull request body lost its bullet.\n",
    )
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "base": {"sha": base},
                    "head": {"sha": bad},
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run_checker("--github-event", str(event), cwd=repository)

    assert result.returncode == 1
    assert result.stderr.startswith(f"{bad[:12]}: line 3:")


def test_github_event_ignores_deleted_ref(tmp_path: Path) -> None:
    """A branch or tag deletion introduces no commit message to validate."""
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "before": "1" * 40,
                "after": "0" * 40,
            }
        ),
        encoding="utf-8",
    )

    result = _run_checker("--github-event", str(event))

    assert result.returncode == 0
    assert result.stdout == "Commit message style passed: 0 commits\n"
    assert result.stderr == ""


def test_commit_msg_hook_uses_the_shared_validator(tmp_path: Path) -> None:
    """The installed hook rejects the same drifting body at commit time."""
    message = _write_message(
        tmp_path,
        "📄 Contracts: Enforce commit message style\n\n"
        "The hook must reject this prose body.\n",
    )

    result = subprocess.run(
        ["sh", str(HOOK), str(message)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr == (
        "commit message: line 3: body lines must start with '- '\n"
    )
