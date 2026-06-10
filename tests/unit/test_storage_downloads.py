# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Tests for the relay-mode download branch of ``storage/downloads.py``.

When relay mode is on, ``_resolve_obs_file`` takes a relay fast path:
``download_obs_file`` fetches the object bytes through the relay and
writes them to a run-scoped local temp file without instantiating an
``ObsClient`` or probing the obsfs mount. Normal mode is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_server_phytomni.storage import downloads as downloads_module
from mcp_server_phytomni.storage.downloads import download_obs_file

pytestmark = pytest.mark.unit


async def test_download_obs_file_uses_relay_in_relay_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relay mode downloads via the relay to a temp file, no ObsClient."""
    relay = Mock()
    relay.get_obs_object = AsyncMock(return_value=b"PDF-BYTES")
    monkeypatch.setattr(downloads_module, "relay_mode_enabled", lambda: True)
    monkeypatch.setattr(
        downloads_module, "current_relay_client", lambda: relay
    )
    no_sdk = Mock()
    monkeypatch.setattr(downloads_module, "ObsClient", no_sdk)

    local_path = await download_obs_file(
        "/obs/phytomni/agent_data/uploads/u/r/up/notes.pdf", str(tmp_path)
    )

    assert relay.get_obs_object.await_count == 1
    assert relay.get_obs_object.await_args.args[0].endswith("notes.pdf")
    assert not no_sdk.called
    assert Path(local_path).read_bytes() == b"PDF-BYTES"
