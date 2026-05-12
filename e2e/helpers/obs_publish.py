# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Per-session demo_data publication to OBS for the live e2e suite.

`publish_demo_data` mirrors every committed file under
``demo_data/docs/`` and ``demo_data/sequences/`` to an OBS prefix
unique to one pytest session, then returns a mapping from the local
relative path to the published ``/obs/<bucket>/...`` URL. Tests use
this map (via ``e2e/conftest.py``'s ``load_payload`` helper) to
rewrite placeholder ``/obs/phytomni/demo/...`` references in committed
payloads to point at the per-session location.

obsfs is preferred when the bucket is mounted; otherwise the OBS SDK
``putFile`` is used as a fallback. The session prefix uses the run id
of the supplied ``RunIdentity`` so multiple concurrent CI runs do not
collide.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from obs import ObsClient, PutObjectHeader

from mcp_server_phytomni.config.defaults import ServerConfig
from mcp_server_phytomni.config.settings import SensitiveConfig
from mcp_server_phytomni.storage.obs_storage import (
    DEFAULT_OBSFS_MOUNT_ROOT,
    obs_path_from_key,
    obsfs_bucket_available,
    obsfs_path_for,
)
from mcp_server_phytomni.storage.path_policy import RunIdentity

_PUBLISH_SUBDIRS = ("docs", "sequences")
_SERVER_CONFIG = ServerConfig()


@dataclass(frozen=True)
class PublishTarget:
    """OBS endpoint, bucket, and mount info used by ``publish_demo_data``.

    Attributes:
        bucket_name: Target OBS bucket.
        obs_server: OBS endpoint URL.
        obsfs_mount_root: Local mount root for obsfs (default ``/obs``).
        access_key_id: HMAC access key for the SDK fallback.
        secret_access_key: HMAC secret for the SDK fallback.
    """

    bucket_name: str
    obs_server: str
    obsfs_mount_root: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_defaults(cls) -> "PublishTarget":
        """Build a publish target from the project's standard config.

        Returns:
            Publish target wired with ``ServerConfig`` defaults and the
            OBS credentials exposed by ``SensitiveConfig``.
        """
        access_key, secret = SensitiveConfig.load().obs_credentials()
        return cls(
            bucket_name=_SERVER_CONFIG.BUCKET_NAME,
            obs_server=_SERVER_CONFIG.OBS_SERVER,
            obsfs_mount_root=DEFAULT_OBSFS_MOUNT_ROOT,
            access_key_id=access_key,
            secret_access_key=secret,
        )


def publish_demo_data(
    demo_data_dir: Path,
    run_identity: RunIdentity,
    *,
    target: PublishTarget | None = None,
) -> Dict[str, str]:
    """Upload demo_data binary fixtures to a per-session OBS prefix.

    Args:
        demo_data_dir: Absolute path to the repository's ``demo_data/``.
        run_identity: Run identity used to scope the OBS prefix.
        target: Optional publish target override; defaults to the
            project's standard ``ServerConfig`` + ``SensitiveConfig``.

    Returns:
        Mapping from local relative path (e.g. ``docs/sample.pdf``) to
        the published ``/obs/<bucket>/<key>`` URL.
    """
    resolved_target = target or PublishTarget.from_defaults()
    obsfs_ready = obsfs_bucket_available(
        resolved_target.bucket_name,
        resolved_target.obsfs_mount_root,
    )
    obs_client = None if obsfs_ready else _build_obs_client(resolved_target)

    published: Dict[str, str] = {}
    for local_path, rel_path in _iter_publishable_files(
        demo_data_dir, _PUBLISH_SUBDIRS
    ):
        object_key = f"agent_data/demo_data/{run_identity.run_id}/{rel_path}"
        published[rel_path] = _publish_one(
            local_path,
            object_key,
            resolved_target,
            obs_client,
        )
    return published


def _iter_publishable_files(
    demo_data_dir: Path,
    subdirs: Iterable[str],
) -> Iterable[tuple[Path, str]]:
    """Yield ``(absolute, relative)`` pairs for every publishable file."""
    for subdir in subdirs:
        base = demo_data_dir / subdir
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob("*")):
            if not entry.is_file():
                continue
            yield entry, entry.relative_to(demo_data_dir).as_posix()


def _publish_one(
    local_path: Path,
    object_key: str,
    target: PublishTarget,
    obs_client: ObsClient | None,
) -> str:
    """Publish one file via obsfs first, then OBS SDK if obsfs missed."""
    if obs_client is None:
        destination = obsfs_path_for(
            object_key,
            target.bucket_name,
            target.obsfs_mount_root,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)
        return obs_path_from_key(target.bucket_name, object_key)

    headers = PutObjectHeader()
    headers.contentType = "application/octet-stream"
    response = obs_client.putFile(
        bucketName=target.bucket_name,
        objectKey=object_key,
        file_path=str(local_path),
        headers=headers,
    )
    status = getattr(response, "status", None)
    if status is None or status >= 300:
        raise OSError(f"OBS putFile failed for {object_key} (status={status})")
    return obs_path_from_key(target.bucket_name, object_key)


def _build_obs_client(target: PublishTarget) -> ObsClient:
    """Return an OBS SDK client for the SDK-fallback upload branch."""
    return ObsClient(
        access_key_id=target.access_key_id,
        secret_access_key=target.secret_access_key,
        server=target.obs_server,
    )
