# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""HTTP capability negotiation and resumable-upload cleanup contracts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from tests.support.http_fakes import open_asgi_client

from mcp_server_phytomni.api import factory as factory_module
from mcp_server_phytomni.api import upload_runtime as upload_runtime_module
from mcp_server_phytomni.api.agent_capabilities import (
    serialize_file_upload_capability,
)
from mcp_server_phytomni.api.app import create_app
from mcp_server_phytomni.api.auth import ApiKeyStore
from mcp_server_phytomni.api.resumable_uploads import (
    ResumableUploadService,
    UploadServiceConfig,
)
from mcp_server_phytomni.api.schemas import (
    UploadCompletionRequest,
    UploadCreateRequest,
)
from mcp_server_phytomni.api.upload_runtime import UploadRuntime
from mcp_server_phytomni.config.defaults import ApiConfig
from mcp_server_phytomni.runtime.resumable_uploads import (
    ResumableUploadRegistry,
    ResumableUploadRegistryConfig,
)
from mcp_server_phytomni.storage.multipart import (
    FakeMultipartStorage,
    MultipartSession,
    MultipartStorageError,
    PartInput,
)

pytestmark = pytest.mark.server

_NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _controlled_thread(
    target: Callable[[], None],
    *,
    daemon: bool,
    starts: list[Callable[[], None]],
) -> Mock:
    """Hold a cleanup target until the test releases it deterministically."""
    assert daemon is True
    return Mock(start=lambda: starts.append(target))


class _OneAbortFailureStorage(FakeMultipartStorage):
    """Fail one provider cleanup with a deliberately sensitive message."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 1

    def abort(self, session: MultipartSession) -> None:
        """Fail the first abort and delegate every later attempt."""
        if self.failures:
            self.failures -= 1
            raise MultipartStorageError("private-provider-exception")
        super().abort(session)


def _nested_keys(value: Any) -> set[str]:
    """Collect JSON object keys for the descriptor redaction assertion."""
    if isinstance(value, dict):
        return set(value) | {
            key for item in value.values() for key in _nested_keys(item)
        }
    if isinstance(value, list):
        return {key for item in value for key in _nested_keys(item)}
    return set()


def test_file_upload_capability_is_sanitized_and_fresh() -> None:
    """The public descriptor has exact routes and no provider coordinates."""
    first = serialize_file_upload_capability()
    second = serialize_file_upload_capability()

    # Protocol identity lives in the top-level `protocols` map of the catalog,
    # not in this descriptor; it carries only the route surface and limits.
    assert "protocol" not in first
    assert first["route_family"] == "resumable_files"
    assert first["limits"] == {
        "max_file_bytes": 10 * 1024**3,
        "part_size_bytes": 128 * 1024**2,
        "max_parallel_parts": 4,
        "max_active_assets": 3,
        "capability_ttl_seconds": 900,
        "session_ttl_seconds": 7 * 24 * 60 * 60,
    }
    assert [route["path"] for route in first["routes"]] == [
        "/v1/files",
        "/v1/files/{asset_id}/capability",
        "/v1/files/{asset_id}",
        "/v1/files/{asset_id}/parts/{part_number}",
        "/v1/files/{asset_id}/complete",
        "/v1/files/{asset_id}",
    ]
    assert not {
        "origin",
        "bucket",
        "object_key",
        "upload_id",
        "token",
        "capability",
    } & _nested_keys(first)

    first["routes"][0]["method"] = "PATCH"
    assert second["routes"][0]["method"] == "POST"


async def test_agents_catalog_advertises_upload_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The native catalog carries the Bot protocol independently of context."""
    keys_path = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", keys_path)
    key = ApiKeyStore(keys_path).create(user_id="catalog-user").api_key

    async with open_asgi_client(
        monkeypatch, create_app(), base_url="https://api.test"
    ) as client:
        response = await client.get(
            "/v1/agents", headers={"Authorization": f"Bearer {key}"}
        )

    assert response.status_code == 200
    body = response.json()
    descriptor = body["file_upload"]
    # The protocol identity lives only in the top-level `protocols` map now;
    # the `file_upload` descriptor carries runtime limits and routes only.
    assert "protocol" not in descriptor
    assert descriptor["limits"]["max_parallel_parts"] == 4
    assert "upload_origin" not in descriptor
    assert body["protocols"]["obs-multipart-v2"] == [2]


async def test_native_agent_run_retains_cleanup_finalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The pre-existing native Agent-run trigger remains a yield dependency."""
    keys_path = str(tmp_path / "keys.sqlite")
    monkeypatch.setenv("PHYTOMNI_API_KEYS_DB", keys_path)
    key = ApiKeyStore(keys_path).create(user_id="agent-user").api_key
    trigger_cleanup = Mock(return_value=True)
    monkeypatch.setattr(UploadRuntime, "trigger_cleanup", trigger_cleanup)

    async with open_asgi_client(
        monkeypatch, create_app(), base_url="https://api.test"
    ) as client:
        response = await client.post(
            "/v1/agents/missing/runs",
            headers={"Authorization": f"Bearer {key}"},
            json={"arguments": {}},
        )

    assert response.status_code == 404
    trigger_cleanup.assert_called_once_with()


def test_cleanup_hook_is_repeatable_and_preserves_completed_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup expires unfinished rows once and preserves completed rows."""
    clock = [_NOW]
    storage = FakeMultipartStorage()
    registry = ResumableUploadRegistry(str(tmp_path / "uploads.sqlite"))
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="test-bucket",
            upload_origin="https://bot.example",
            now=lambda: clock[0],
        ),
    )
    expired = service.create(_create_request("expired"))
    completed = service.create(_create_request("completed"))
    body = b"abc"
    service.put_part(
        completed.asset_id,
        completed.capability,
        PartInput(1, BytesIO(body), len(body), sha256(body).hexdigest()),
    )
    service.complete(
        completed.asset_id,
        completed.capability,
        _completion_request(),
    )

    runtime = UploadRuntime(
        config_factory=getattr(factory_module, "_api_config"),
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(
        UploadRuntime,
        "get_upload_service",
        lambda _runtime: service,
    )
    clock[0] = _NOW + timedelta(days=8)

    assert runtime.cleanup_expired() == (expired.asset_id,)
    assert runtime.cleanup_expired() == ()
    expired_asset = registry.get_asset(expired.asset_id, owner="owner-1")
    completed_asset = registry.get_asset(completed.asset_id, owner="owner-1")
    assert expired_asset is not None
    assert completed_asset is not None
    assert expired_asset.status == "expired"
    assert completed_asset.status == "completed"


def test_upload_runtime_applies_configured_provisional_ttl(
    tmp_path: Path,
) -> None:
    """The production factory passes the bounded provisional TTL through."""
    config = ApiConfig(
        API_TASKS_DB_PATH=str(tmp_path / "uploads.sqlite"),
        API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS=60,
    )
    runtime = UploadRuntime(
        config_factory=lambda: config,
        logger=logging.getLogger(__name__),
    )

    service = runtime.get_upload_service()

    assert service.registry.provisional_ttl == timedelta(seconds=60)


def test_cleanup_trigger_starts_at_most_one_worker_per_interval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A request burst claims one worker without creating waiting threads."""
    clock = [100.0]
    starts: list[Callable[[], None]] = []
    config = ApiConfig(
        API_TASKS_DB_PATH=str(tmp_path / "uploads.sqlite"),
        API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS=30,
    )
    runtime = UploadRuntime(
        config_factory=lambda: config,
        logger=logging.getLogger(__name__),
    )
    monkeypatch.setattr(upload_runtime_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        upload_runtime_module,
        "Thread",
        lambda target, *, daemon: _controlled_thread(
            target, daemon=daemon, starts=starts
        ),
    )

    with ThreadPoolExecutor(max_workers=12) as executor:
        claimed = list(
            executor.map(lambda _index: runtime.trigger_cleanup(), range(48))
        )

    assert claimed.count(True) == 1
    assert len(starts) == 1
    starts.pop()()

    clock[0] = 129.0
    assert runtime.trigger_cleanup() is False
    assert not starts
    clock[0] = 130.0
    assert runtime.trigger_cleanup() is True
    assert len(starts) == 1


def test_cleanup_worker_exception_clears_running_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unexpected worker failure stays contained and releases its slot."""
    clock = [100.0]
    starts: list[Callable[[], None]] = []
    logger = logging.getLogger("tests.upload.cleanup.worker")
    config = ApiConfig(
        API_TASKS_DB_PATH=str(tmp_path / "uploads.sqlite"),
        API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS=30,
    )
    runtime = UploadRuntime(config_factory=lambda: config, logger=logger)
    monkeypatch.setattr(upload_runtime_module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        upload_runtime_module,
        "Thread",
        lambda target, *, daemon: _controlled_thread(
            target, daemon=daemon, starts=starts
        ),
    )
    monkeypatch.setattr(
        runtime,
        "get_upload_service",
        lambda: (_ for _ in ()).throw(
            RuntimeError("private-worker-exception")
        ),
    )

    with caplog.at_level(logging.WARNING, logger=logger.name):
        assert runtime.trigger_cleanup() is True
        starts.pop()()

    assert "private-worker-exception" not in caplog.text
    clock[0] = 130.0
    assert runtime.trigger_cleanup() is True
    assert len(starts) == 1


def test_cleanup_logs_one_redacted_summary_per_working_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup reports counts without upload or provider identities."""
    clock = [_NOW]
    storage = _OneAbortFailureStorage()
    registry = ResumableUploadRegistry(
        str(tmp_path / "uploads.sqlite"),
        ResumableUploadRegistryConfig(provisional_ttl=timedelta(minutes=1)),
    )
    service = ResumableUploadService(
        registry,
        storage,
        UploadServiceConfig(
            bucket_name="private-bucket",
            upload_origin="https://private-upload.example",
            now=lambda: clock[0],
        ),
    )
    assets = {
        name: service.create(_create_request(f"private-{name}"))
        for name in ("provisional", "normal", "aborted")
    }
    service.head(assets["normal"].asset_id, assets["normal"].capability)
    service.abort(assets["aborted"].asset_id, assets["aborted"].capability)
    clock[0] = _NOW + timedelta(days=8)
    logger = logging.getLogger("tests.upload.cleanup.summary")
    runtime = UploadRuntime(
        config_factory=getattr(factory_module, "_api_config"),
        logger=logger,
        upload_service=service,
    )
    monotonic_clock = [100.0]
    monkeypatch.setattr(
        upload_runtime_module, "monotonic", lambda: monotonic_clock[0]
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        assert set(runtime.cleanup_expired()) == {
            item.asset_id for item in assets.values()
        }
        assert runtime.cleanup_expired() == ()
        monotonic_clock[0] = 400.0
        retried = runtime.cleanup_expired()
        monotonic_clock[0] = 700.0
        assert runtime.cleanup_expired() == ()

    assert len(retried) == 1
    assert retried[0] in {item.asset_id for item in assets.values()}
    summaries = [
        record.getMessage()
        for record in caplog.records
        if record.name == logger.name and record.levelno == logging.INFO
    ]
    assert summaries == [
        "Upload cleanup summary: provisional_expired=1 "
        "normal_expired=1 provider_attempts=3 provider_succeeded=2 "
        "provider_pending_retries=1",
        "Upload cleanup summary: provisional_expired=0 "
        "normal_expired=0 provider_attempts=1 provider_succeeded=1 "
        "provider_pending_retries=0",
    ]
    forbidden_values = {
        "owner-1",
        "private-provisional.fa",
        "private-normal.fa",
        "private-aborted.fa",
        "private-bucket",
        "https://private-upload.example",
        "private-provider-exception",
        *(item.asset_id for item in assets.values()),
        *(item.capability for item in assets.values()),
    }
    for state in storage.sessions.values():
        forbidden_values.update(
            {
                state.session.object_key,
                state.session.upload_id,
            }
        )
    assert not any(value in caplog.text for value in forbidden_values)


def _create_request(key: str) -> UploadCreateRequest:
    """Build a small service request without coupling to HTTP fixtures."""
    return UploadCreateRequest(
        owner_subject="owner-1",
        filename=f"{key}.fa",
        content_type="application/octet-stream",
        size_bytes=3,
        purpose="chat_attachment",
        idempotency_key=key,
    )


def _completion_request() -> UploadCompletionRequest:
    """Build the optional completion checksum request."""
    return UploadCompletionRequest(sha256=sha256(b"abc").hexdigest())
