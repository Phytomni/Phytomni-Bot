# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Lazy Bot-owned runtime for resumable upload storage and resolution."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config.defaults import ApiConfig, ServerConfig
from ..runtime.background_submission import BACKGROUND_RUNTIME_ERRORS
from ..runtime.resumable_uploads import (
    ResumableUploadRegistry,
    ResumableUploadRegistryConfig,
)
from ..storage.multipart import BoundedMultipartStorage
from .agent_capabilities import (
    serialize_file_upload_capability as _serialize_file_upload_capability,
)
from .app_support import _ErrorResponseOptions
from .asset_resolver import AssetResolver
from .resumable_uploads import (
    ResumableUploadService,
    UploadContractError,
    UploadServiceConfig,
)

__all__ = [
    "UploadRuntime",
    "install_upload_cors",
    "register_upload_error_handler",
]


@dataclass
class UploadRuntime:
    """Own one process-local upload service and its cleanup lifecycle."""

    config_factory: Callable[[], ApiConfig]
    logger: logging.Logger
    upload_service: ResumableUploadService | None = None
    asset_resolver: AssetResolver | None = None
    _cleanup_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _last_cleanup: float = field(default=0.0, init=False, repr=False)

    def get_upload_service(self) -> ResumableUploadService:
        """Build the upload service at the Bot storage boundary once."""
        if self.upload_service is None:
            config = self.config_factory()
            server_config = ServerConfig()
            registry = ResumableUploadRegistry(
                config.API_TASKS_DB_PATH,
                ResumableUploadRegistryConfig(
                    max_upload_bytes=config.API_UPLOAD_V2_MAX_BYTES,
                    part_size_bytes=config.API_UPLOAD_V2_PART_SIZE_BYTES,
                    session_ttl=timedelta(
                        seconds=config.API_UPLOAD_V2_SESSION_TTL_SECONDS
                    ),
                    capability_ttl=timedelta(
                        seconds=config.API_UPLOAD_V2_CAPABILITY_TTL_SECONDS
                    ),
                ),
            )
            self.upload_service = ResumableUploadService(
                registry,
                BoundedMultipartStorage(obs_server=server_config.OBS_SERVER),
                UploadServiceConfig(
                    bucket_name=config.API_UPLOAD_V2_BUCKET,
                    upload_origin=config.API_UPLOAD_V2_ORIGIN,
                    max_upload_bytes=config.API_UPLOAD_V2_MAX_BYTES,
                    part_size_bytes=config.API_UPLOAD_V2_PART_SIZE_BYTES,
                    max_parallel_parts=(
                        config.API_UPLOAD_V2_MAX_PARALLEL_PARTS
                    ),
                ),
            )
        return self.upload_service

    def get_asset_resolver(self) -> AssetResolver:
        """Build the owner-scoped resolver on the upload service state."""
        if self.asset_resolver is None:
            config = self.config_factory()
            service = self.get_upload_service()
            workspace_root = (
                Path(config.API_TASKS_DB_PATH).parent / "upload-materialized"
            )
            self.asset_resolver = AssetResolver(
                service.registry,
                service.storage.download_to_path,
                bucket_name=config.API_UPLOAD_V2_BUCKET,
                workspace_root=workspace_root,
            )
        return self.asset_resolver

    def serialize_file_upload_capability(self) -> dict[str, Any]:
        """Serialize the configured public upload limits."""
        config = self.config_factory()
        return _serialize_file_upload_capability(
            {
                "max_file_bytes": config.API_UPLOAD_V2_MAX_BYTES,
                "part_size_bytes": config.API_UPLOAD_V2_PART_SIZE_BYTES,
                "max_parallel_parts": config.API_UPLOAD_V2_MAX_PARALLEL_PARTS,
                "capability_ttl_seconds": (
                    config.API_UPLOAD_V2_CAPABILITY_TTL_SECONDS
                ),
                "session_ttl_seconds": (
                    config.API_UPLOAD_V2_SESSION_TTL_SECONDS
                ),
            }
        )

    async def schedule_cleanup(self, background: BackgroundTasks) -> None:
        """Schedule one rate-limited cleanup pass after a request."""
        background.add_task(self._start_cleanup_worker)

    async def _start_cleanup_worker(self) -> None:
        """Start cleanup without making the response await provider I/O."""
        Thread(target=self._cleanup_expired_best_effort, daemon=True).start()

    def _cleanup_expired_best_effort(self) -> None:
        """Run cleanup in a daemon worker and contain unexpected failures."""
        try:
            self.cleanup_expired()
        except BACKGROUND_RUNTIME_ERRORS as error:
            self.logger.warning(
                "resumable upload cleanup failed: %s",
                error.__class__.__name__,
            )

    def cleanup_expired(
        self,
        service_factory: Callable[[], ResumableUploadService] | None = None,
    ) -> tuple[str, ...]:
        """Run a rate-limited, repeatable upload-session cleanup pass."""
        interval = self.config_factory().API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS
        current = monotonic()
        with self._cleanup_lock:
            if self._last_cleanup and current - self._last_cleanup < interval:
                return ()
            self._last_cleanup = current
        try:
            service = (
                self.get_upload_service()
                if service_factory is None
                else service_factory()
            )
            return service.cleanup_expired()
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self.logger.warning(
                "resumable upload cleanup failed: %s",
                error.__class__.__name__,
            )
            return ()


def install_upload_cors(app: FastAPI, allowed_origins: list[str]) -> None:
    """Install the browser data-plane CORS policy on the API app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["HEAD", "PUT", "POST", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Phytomni-Part-SHA256",
        ],
        expose_headers=[
            "Upload-Protocol",
            "Upload-Status",
            "Upload-Length",
            "Upload-Part-Size",
            "Upload-Part-Count",
            "Upload-Received-Parts",
            "Retry-After",
            "X-Request-Id",
        ],
    )


def register_upload_error_handler(
    app: FastAPI,
    error_response: Callable[..., JSONResponse],
) -> None:
    """Register the sanitized error handler for upload contract failures."""

    @app.exception_handler(UploadContractError)
    async def upload_contract_exception_handler(
        _request: Request,
        exc: UploadContractError,
    ) -> JSONResponse:
        """Render upload failures without exposing provider coordinates."""
        return error_response(
            exc.status_code,
            str(exc),
            options=_ErrorResponseOptions(
                code=exc.code,
                retryable=exc.retryable,
                headers={"Cache-Control": "no-store"},
            ),
        )
