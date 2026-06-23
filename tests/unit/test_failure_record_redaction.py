# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""``redact_failure_message`` relocation + re-export invariants."""

from __future__ import annotations

import pytest

from mcp_server_phytomni.agents.shared.parallel_dispatch import (
    redact_failure_message,
)
from mcp_server_phytomni.mcp.universal_failures import (
    redact_failure_message as _reexported_redact,
)

pytestmark = pytest.mark.unit


def test_redact_relocated_to_shared_and_reexported() -> None:
    """The helper lives in agents/shared and re-exports from mcp."""
    assert redact_failure_message is _reexported_redact


def test_redact_strips_urls_and_secrets() -> None:
    """Behaviour is unchanged after the move."""
    assert (
        redact_failure_message("connect failed for https://host:9000/x")
        == "connect failed for <redacted-url>"
    )
    assert redact_failure_message("token=deadbeef") == "<redacted-secret>"
    # ``Bearer`` redacts first, then the ``authorization:`` keyword pass
    # consumes the whole fragment — the token never leaks either way.
    assert (
        redact_failure_message("Authorization: Bearer abc.def")
        == "<redacted-secret>"
    )


def test_redact_url_query_bearer_concatenation_leaks_no_token() -> None:
    """A token after a ``...=Bearer `` URL tail must not survive.

    A malformed failure string can splice a request URL whose query ends
    in ``...=Bearer`` with the token on the far side of a space
    (``https://h/p?Authorization=Bearer tok``). A URL-first pass lets the
    greedy ``\\S+`` swallow the URL up to the space and strand the token;
    the credential passes must run before the URL pass so it never leaks.
    """
    scrubbed = redact_failure_message(
        "https://host/path?Authorization=Bearer abc.def"
    )
    assert "abc.def" not in scrubbed
    assert "<redacted" in scrubbed
