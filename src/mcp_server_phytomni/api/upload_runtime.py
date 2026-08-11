# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
"""Lazy Bot-owned runtime for resumable upload storage and resolution."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config.defaults import ApiConfig
from ..runtime.background_submission import BACKGROUND_RUNTIME_ERRORS
from ..runtime.outbound import current_outbound_runtime
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
    UploadCleanupResult,
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
    _last_cleanup: float | None = field(default=None, init=False, repr=False)
    _cleanup_running: bool = field(default=False, init=False, repr=False)

    def get_upload_service(self) -> ResumableUploadService:
        """Build the upload service at the Bot storage boundary once."""
        if self.upload_service is None:
            config = self.config_factory()
            outbound = current_outbound_runtime()
            if outbound.obs is None:
                raise RuntimeError("OBS runtime is unavailable")
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
                    provisional_ttl=timedelta(
                        seconds=config.API_UPLOAD_V2_PROVISIONAL_TTL_SECONDS
                    ),
                ),
            )
            self.upload_service = ResumableUploadService(
                registry,
                BoundedMultipartStorage(
                    runtime=outbound.obs,
                    loop=asyncio.get_running_loop(),
                ),
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

    async def schedule_cleanup(self) -> AsyncIterator[None]:
        """Trigger non-blocking cleanup after a dependent request settles."""
        try:
            yield
        finally:
            self.trigger_cleanup()

    def trigger_cleanup(self) -> bool:
        """Start at most one process-local cleanup worker per interval."""
        try:
            claimed = self._claim_cleanup_slot()
        except BACKGROUND_RUNTIME_ERRORS as error:
            self._log_cleanup_failure(error)
            return False
        if not claimed:
            return False
        try:
            Thread(
                target=self._cleanup_expired_best_effort,
                daemon=True,
            ).start()
        except BACKGROUND_RUNTIME_ERRORS as error:
            self._release_cleanup_slot(reset_interval=True)
            self._log_cleanup_failure(error)
            return False
        return True

    def _cleanup_expired_best_effort(self) -> None:
        """Run cleanup in a daemon worker and contain unexpected failures."""
        try:
            self._cleanup_expired()
        except BACKGROUND_RUNTIME_ERRORS as error:
            self._log_cleanup_failure(error)
        finally:
            self._release_cleanup_slot()

    def cleanup_expired(
        self,
        service_factory: Callable[[], ResumableUploadService] | None = None,
    ) -> tuple[str, ...]:
        """Run a rate-limited, repeatable upload-session cleanup pass."""
        if not self._claim_cleanup_slot():
            return ()
        try:
            return self._cleanup_expired(service_factory)
        except (OSError, RuntimeError, sqlite3.Error) as error:
            self._log_cleanup_failure(error)
            return ()
        finally:
            self._release_cleanup_slot()

    def _cleanup_expired(
        self,
        service_factory: Callable[[], ResumableUploadService] | None = None,
    ) -> tuple[str, ...]:
        """Run one claimed pass and project its sanitized aggregate summary."""
        service = (
            self.get_upload_service()
            if service_factory is None
            else service_factory()
        )
        outcome = service.cleanup_expired_outcome()
        if _cleanup_work_occurred(outcome):
            self.logger.info(
                "Upload cleanup summary: provisional_expired=%d "
                "normal_expired=%d provider_attempts=%d "
                "provider_succeeded=%d provider_pending_retries=%d",
                outcome.provisional_expired,
                outcome.normal_expired,
                outcome.provider_attempts,
                outcome.provider_succeeded,
                outcome.provider_pending_retries,
            )
        return outcome.asset_ids

    def _claim_cleanup_slot(self) -> bool:
        """Atomically enforce both worker exclusion and the start interval."""
        interval = self.config_factory().API_UPLOAD_V2_CLEANUP_INTERVAL_SECONDS
        current = monotonic()
        with self._cleanup_lock:
            if self._cleanup_running:
                return False
            if (
                self._last_cleanup is not None
                and current - self._last_cleanup < interval
            ):
                return False
            self._cleanup_running = True
            self._last_cleanup = current
            return True

    def _release_cleanup_slot(self, *, reset_interval: bool = False) -> None:
        """Release the process-local worker claim after every exit path."""
        with self._cleanup_lock:
            self._cleanup_running = False
            if reset_interval:
                self._last_cleanup = None

    def _log_cleanup_failure(self, error: Exception) -> None:
        """Log only an exception class, never provider or upload values."""
        self.logger.warning(
            "resumable upload cleanup failed: %s",
            error.__class__.__name__,
        )


def _cleanup_work_occurred(outcome: UploadCleanupResult) -> bool:
    """Return whether an aggregate summary carries observable cleanup work."""
    return any(
        (
            outcome.provisional_expired,
            outcome.normal_expired,
            outcome.provider_attempts,
            outcome.provider_succeeded,
            outcome.provider_pending_retries,
        )
    )


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
