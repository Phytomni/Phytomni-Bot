# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Key-safe asynchronous client for the Phytomni HTTP run API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from mcp_server_phytomni.contracts.deep_genome import (
    DEEP_GENOME_PROGRESS_FIELDS,
    DEEP_GENOME_REPORT_FIELDS,
    DeepGenomeReportSnapshot,
    sanitize_nonnegative_int,
)

_RUN_STATUSES = frozenset({"running", "input_required", "succeeded", "failed"})
_REPORT_STAGES = frozenset({"waiting_for_brief_gene", "intermediate", "final"})
_REPORT_COMPLETENESS = frozenset({"none", "partial", "complete"})

__all__ = [
    "HttpClientError",
    "PhytomniHttpClient",
    "RunProtocolError",
    "RunSnapshot",
    "SubmittedRun",
]


class HttpClientError(RuntimeError):
    """Raised for transport failures or unexpected HTTP statuses."""


class RunProtocolError(HttpClientError):
    """Raised when a successful response does not match the run contract."""


@dataclass(frozen=True)
class SubmittedRun:
    """Accepted run identity returned by the asynchronous submit endpoint."""

    run_id: str
    task_ids: tuple[str, ...] = ()
    status: str | None = None


@dataclass(frozen=True)
class _RunIdentity:
    """Identity fields shared by one public run snapshot."""

    run_id: str
    status: str


@dataclass(frozen=True)
class _RunAnswer:
    """Answer field kept ahead of the shared report projection."""

    answer: str | None = None


@dataclass(frozen=True)
class _RunReport(DeepGenomeReportSnapshot, _RunAnswer):
    """Report fields shared by one public run snapshot."""


@dataclass(frozen=True)
class _RunHealth:
    """Progress and degradation fields shared by one public snapshot."""

    progress: Mapping[str, int | bool | str] = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str | None = None
    failures: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class RunSnapshot(_RunHealth, _RunReport, _RunIdentity):
    """Public run state without remote child identities or raw payloads."""


class PhytomniHttpClient:
    """Call authenticated asynchronous run endpoints without leaking secrets.

    The optional ``httpx.AsyncClient`` is caller-owned and is never closed by
    this wrapper. When it is omitted, a private client is created lazily and
    closed by the async context manager.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _normalize_base_url(base_url)
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api key is required")
        self._api_key = api_key
        self._client = client
        self._owns_client = client is None

    def __repr__(self) -> str:
        """Return a representation that omits the API key and URL query."""
        return f"PhytomniHttpClient(base_url={self._base_url!r})"

    async def __aenter__(self) -> PhytomniHttpClient:
        """Return this client for async context-manager use."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        """Close only the private HTTP client, never an injected client."""
        del exc_type, exc, traceback
        await self.aclose()

    async def aclose(self) -> None:
        """Close the private transport when one was created by this wrapper."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def submit(
        self,
        agent: str,
        arguments: Mapping[str, Any],
    ) -> SubmittedRun:
        """Submit one agent run and validate its accepted identity."""
        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent is required")
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        payload = await self._request(
            "POST",
            ("v1", "agents", agent, "runs"),
            expected_status=202,
            json_body={"arguments": dict(arguments)},
        )
        body = _require_mapping(payload, "submit")
        run_id = _required_text(body.get("id"), "submit id")
        task_ids = _optional_task_ids(body.get("task_ids"))
        status = _optional_status(body.get("status"))
        return SubmittedRun(run_id=run_id, task_ids=task_ids, status=status)

    async def get_run(self, run_id: str) -> RunSnapshot:
        """Read one owner-scoped run snapshot without remote polling."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run id is required")
        payload = await self._request(
            "GET",
            ("v1", "runs", run_id),
            expected_status=200,
        )
        body = _require_mapping(payload, "run")
        resolved_run_id = _required_text(
            body.get("run_id", body.get("id")), "run id"
        )
        status = body.get("status")
        if status not in _RUN_STATUSES:
            raise RunProtocolError("run response has an unsupported status")

        result_value = body.get("result")
        if result_value is None:
            result: Mapping[str, Any] = {}
        else:
            result = _require_mapping(result_value, "run result")
        answer = _optional_text(body.get("answer"))
        if answer is None:
            formatted = result.get("formatted")
            if isinstance(formatted, Mapping):
                answer = _optional_text(formatted.get("answer"))
        degraded = result.get("degraded")
        if not isinstance(degraded, bool):
            degraded = False
        return _new_run_snapshot(
            {
                "run_id": resolved_run_id,
                "status": status,
                "answer": answer,
                "intermediate_report": _optional_text(
                    result.get("intermediate_report")
                ),
                "final_report": _optional_text(result.get("final_report")),
                "report_stage": _optional_choice(
                    result.get("report_stage"),
                    _REPORT_STAGES,
                    "report stage",
                    "waiting_for_brief_gene",
                ),
                "report_completeness": _optional_choice(
                    result.get("report_completeness"),
                    _REPORT_COMPLETENESS,
                    "report completeness",
                    "none",
                ),
                "report_revision": sanitize_nonnegative_int(
                    result.get("report_revision")
                ),
                "report_updated_at": _optional_text(
                    result.get("report_updated_at")
                ),
                "progress": _public_progress(result.get("progress")),
                "degraded": degraded,
                "degraded_reason": _optional_text(
                    result.get("degraded_reason")
                ),
                "failures": _public_failures(result.get("failures")),
            }
        )

    async def _request(
        self,
        method: str,
        path_parts: Sequence[str],
        *,
        expected_status: int,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        """Issue one request and return decoded JSON without echoing data."""
        client = self._get_client()
        try:
            response = await client.request(
                method,
                self._url(path_parts),
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=json_body,
            )
        except httpx.TimeoutException:
            raise HttpClientError("HTTP request timed out") from None
        except httpx.RequestError:
            raise HttpClientError("HTTP request failed") from None
        if response.status_code != expected_status:
            raise HttpClientError(
                f"HTTP request failed with status {response.status_code}"
            )
        try:
            return response.json()
        except ValueError:
            raise RunProtocolError(
                "HTTP response was not valid JSON"
            ) from None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the injected or lazily-created transport."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    def _url(self, path_parts: Sequence[str]) -> str:
        """Build a path-only URL with each dynamic component quoted."""
        path = self._base_url
        for part in path_parts:
            path = f"{path}/{quote(part, safe='')}"
        return path


_RUN_SNAPSHOT_FIELDS = (
    "run_id",
    "status",
    "answer",
    *DEEP_GENOME_REPORT_FIELDS,
)


def _new_run_snapshot(values: Mapping[str, Any]) -> RunSnapshot:
    """Build the inherited dataclass without exposing child-id fields."""
    snapshot = object.__new__(RunSnapshot)
    for field_name in _RUN_SNAPSHOT_FIELDS:
        object.__setattr__(snapshot, field_name, values[field_name])
    return snapshot


def _normalize_base_url(value: str) -> str:
    """Normalize a base URL while discarding query and fragment components."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base URL is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL credentials are not supported")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    """Require a JSON object without echoing its contents."""
    if not isinstance(value, Mapping):
        raise RunProtocolError(f"{label} response must be a JSON object")
    return value


def _required_text(value: Any, label: str) -> str:
    """Require a nonblank string field."""
    if not isinstance(value, str) or not value.strip():
        raise RunProtocolError(f"{label} is missing or invalid")
    return value


def _optional_text(value: Any) -> str | None:
    """Return a nonblank string field or ``None``."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _optional_status(value: Any) -> str | None:
    """Validate an optional submit status."""
    if value is None:
        return None
    if value not in _RUN_STATUSES:
        raise RunProtocolError("submit response has an unsupported status")
    return value


def _optional_task_ids(value: Any) -> tuple[str, ...]:
    """Validate optional accepted task identifiers."""
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RunProtocolError("submit task_ids must be a JSON array")
    task_ids: list[str] = []
    for item in value:
        task_ids.append(_required_text(item, "submit task id"))
    return tuple(task_ids)


def _optional_choice(
    value: Any,
    choices: frozenset[str],
    label: str,
    default: str,
) -> str:
    """Return a validated public enum or its safe default."""
    if value is None:
        return default
    if value not in choices:
        raise RunProtocolError(f"{label} is invalid")
    return value


def _public_progress(value: Any) -> Mapping[str, int | bool | str]:
    """Keep only the documented public progress keys and value types."""
    if not isinstance(value, Mapping):
        return {}
    projected: dict[str, int | bool | str] = {}
    for key in DEEP_GENOME_PROGRESS_FIELDS:
        item = value.get(key)
        if (
            isinstance(item, bool)
            or (isinstance(item, int) and item >= 0)
            or (isinstance(item, str) and item.strip())
        ):
            projected[key] = item
    return projected


def _public_failures(value: Any) -> tuple[Mapping[str, str], ...]:
    """Keep the fixed public failure fields and drop arbitrary nested data."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    failures: list[Mapping[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        work_item_key = _optional_text(item.get("work_item_key"))
        status = _optional_text(item.get("status"))
        message = _optional_text(item.get("message"))
        if work_item_key and status and message:
            failures.append(
                {
                    "work_item_key": work_item_key,
                    "status": status,
                    "message": message,
                }
            )
    return tuple(failures)
