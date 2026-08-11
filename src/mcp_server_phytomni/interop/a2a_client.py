# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Send bounded, resumable messages to operator-approved A2A peers.

Only registry target ids and allowlisted capability ids cross this public
boundary.  The official A2A v1 ``Client`` owns protocol framing; this module
owns policy, timeout, cleanup, payload normalization, and safe observability.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Any, NamedTuple, cast

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import AgentCard, SendMessageRequest

from ..config.settings import SensitiveConfig
from ..runtime.outbound import current_outbound_runtime
from ..storage.path_policy import IdFactory
from .a2a_discovery import InteropA2AError, fetch_external_a2a_card
from .a2a_mapping import (
    A2AMappingError,
    ExternalA2AEvent,
    build_user_message,
    map_stream_response,
)
from .http_transport import InteropHTTPError, httpx_client_factory
from .models import A2ATarget
from .registry import InteropRegistry, InteropRegistryError
from .runtime import InteropResourceRuntime
from .security import AsyncDNSResolver, resolve_host

LOGGER = logging.getLogger(__name__)
_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_TARGET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

AuditSink = Callable[[Mapping[str, object]], object]


@dataclass(frozen=True, slots=True)
class _AuditContext:
    """Safe fields shared by a request's start and terminal events."""

    request_id: str
    target_id: str
    capability: str
    started: float
    sink: AuditSink | None


class InteropA2AClientError(RuntimeError):
    """Sanitized failure from the external A2A client boundary."""

    def __init__(self, code: str, target_id: str) -> None:
        super().__init__(f"external A2A {code} for target {target_id!r}")
        self.code = code
        self.target_id = target_id


A2AClientError = InteropA2AClientError


def _target(target_id: str, registry: InteropRegistry) -> A2ATarget:
    """Resolve one enabled A2A target without accepting a peer URL."""
    if not registry.enabled:
        raise InteropA2AClientError("disabled", target_id)
    try:
        target = registry.require_target(target_id)
    except InteropRegistryError:
        raise InteropA2AClientError("unknown_target", target_id) from None
    if not isinstance(target, A2ATarget):
        raise InteropA2AClientError("unsupported_transport", target_id)
    return target


def _audit_label(value: object, pattern: re.Pattern[str]) -> str:
    """Keep untrusted labels out of structured events unless allowlisted."""
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return "invalid"


def _remote_capability(
    target_id: str,
    capability_id: str,
    target: A2ATarget,
) -> str:
    """Validate a raw or qualified allowlisted skill id."""
    if not isinstance(capability_id, str):
        raise InteropA2AClientError("invalid_capability", target_id)
    prefix = f"{target_id}__"
    remote_name = (
        capability_id.removeprefix(prefix)
        if capability_id.startswith(prefix)
        else capability_id
    )
    if not _CAPABILITY_ID.fullmatch(remote_name or ""):
        raise InteropA2AClientError("invalid_capability", target_id)
    if remote_name not in target.allowed_skills:
        raise InteropA2AClientError("capability_not_allowed", target_id)
    return remote_name


def _timeout(
    requested: float | None,
    configured: float,
    *,
    code: str,
    target_id: str,
) -> float:
    """Use a caller-shortened timeout, never a caller-expanded one."""
    value = configured if requested is None else requested
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InteropA2AClientError(code, target_id)
    if value <= 0:
        raise InteropA2AClientError(code, target_id)
    return min(float(value), configured)


def _factory_kwargs(
    registry: InteropRegistry,
    sensitive_config: SensitiveConfig | None,
    resolver: AsyncDNSResolver,
) -> dict[str, Any]:
    """Build only operator-owned arguments for card/client factories."""
    kwargs: dict[str, Any] = {"registry": registry, "resolver": resolver}
    if sensitive_config is not None:
        kwargs["sensitive_config"] = sensitive_config
    return kwargs


def _audit_event(
    context: _AuditContext,
    terminal_status: str | None,
    error_code: str | None = None,
) -> None:
    """Emit a non-persistent event containing no peer payload or endpoint."""
    event: dict[str, object] = {
        "request_id": context.request_id,
        "target_id": context.target_id,
        "target_kind": "a2a",
        "capability": context.capability,
        "terminal_status": terminal_status,
        "latency_ms": round((monotonic() - context.started) * 1000, 3),
        "error_code": error_code,
    }
    LOGGER.info("external_a2a_event", extra={"interop_event": event})
    if context.sink is not None:
        with suppress(Exception):
            context.sink(event)


async def _close_iterator(iterator: object | None) -> None:
    """Close an SDK async iterator without allowing peer errors to escape."""
    close = getattr(iterator, "aclose", None)
    if not callable(close):
        return
    with suppress(Exception):
        result = close()
        if inspect.isawaitable(result):
            await _await_cleanup(cast(Awaitable[object], result))


async def _close_client(client: object | None) -> None:
    """Close an SDK client or raw HTTPX client, sync or async."""
    close = getattr(client, "close", None)
    if not callable(close):
        return
    with suppress(Exception):
        result = close()
        if inspect.isawaitable(result):
            await _await_cleanup(cast(Awaitable[object], result))


async def _await_cleanup(awaitable: Awaitable[object]) -> None:
    """Finish resource cleanup even when the parent task is cancelled."""
    cleanup = asyncio.ensure_future(awaitable)
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    with suppress(Exception, asyncio.CancelledError):
        cleanup.result()


class _ExecutionState(NamedTuple):
    """Validated request state shared by card, client, and stream helpers."""

    total: float
    idle: float
    request: SendMessageRequest
    remote_capability: str
    factory_kwargs: dict[str, Any]
    card_kwargs: dict[str, Any]
    card_fetcher: Callable[..., Awaitable[AgentCard]]
    client_factory: Callable[..., httpx.AsyncClient]
    sdk_factory: type[ClientFactory]
    interop_runtime: InteropResourceRuntime | None


_OPTION_KEYS = frozenset(
    {
        "text",
        "data",
        "task_id",
        "context_id",
        "sensitive_config",
        "resolver",
        "total_timeout_seconds",
        "idle_timeout_seconds",
        "audit_sink",
        "event_sink",
        "_client_factory",
        "_card_fetcher",
        "_sdk_factory",
        "_interop_runtime",
    }
)


def _prepare_execution(
    target_id: str,
    capability_id: str,
    registry: InteropRegistry,
    options: Mapping[str, Any],
    request_id: str,
) -> _ExecutionState:
    """Resolve policy and build one SDK request without opening a peer."""
    if set(options) - _OPTION_KEYS:
        raise InteropA2AClientError("invalid_request", target_id)
    total, idle, request, remote_capability = _prepare_request_state(
        target_id,
        capability_id,
        registry,
        options,
        request_id,
    )
    sensitive_config = cast(
        SensitiveConfig | None,
        options.get("sensitive_config"),
    )
    resolver = cast(
        AsyncDNSResolver,
        options.get("resolver") or resolve_host,
    )
    factory_kwargs = _factory_kwargs(
        registry,
        sensitive_config,
        resolver,
    )
    interop_runtime = cast(
        InteropResourceRuntime | None,
        options.get("_interop_runtime"),
    )
    if interop_runtime is None and not any(
        key in options
        for key in ("_client_factory", "_card_fetcher", "_sdk_factory")
    ):
        try:
            interop_runtime = current_outbound_runtime().interop
        except (ImportError, RuntimeError):
            interop_runtime = None
        if interop_runtime is None:
            raise InteropA2AClientError("runtime_unavailable", target_id)
    client_factory = cast(
        Callable[..., httpx.AsyncClient],
        options.get("_client_factory") or httpx_client_factory,
    )
    card_kwargs = dict(factory_kwargs)
    if options.get("_client_factory") is not None:
        card_kwargs["_client_factory"] = client_factory
    if options.get("_card_fetcher") is None and interop_runtime is not None:
        card_kwargs["_interop_runtime"] = interop_runtime
    return _ExecutionState(
        total=total,
        idle=idle,
        request=request,
        remote_capability=remote_capability,
        factory_kwargs=factory_kwargs,
        card_kwargs=card_kwargs,
        card_fetcher=cast(
            Callable[..., Awaitable[AgentCard]],
            options.get("_card_fetcher") or fetch_external_a2a_card,
        ),
        client_factory=client_factory,
        sdk_factory=cast(
            type[ClientFactory], options.get("_sdk_factory") or ClientFactory
        ),
        interop_runtime=interop_runtime,
    )


def _prepare_request_state(
    target_id: str,
    capability_id: str,
    registry: InteropRegistry,
    options: Mapping[str, Any],
    request_id: str,
) -> tuple[float, float, SendMessageRequest, str]:
    """Resolve request policy and build the bounded SDK message."""
    target = _target(target_id, registry)
    remote_capability = _remote_capability(
        target_id,
        capability_id,
        target,
    )
    total = _timeout(
        cast(float | None, options.get("total_timeout_seconds")),
        target.total_timeout_seconds,
        code="invalid_total_timeout",
        target_id=target_id,
    )
    idle = _timeout(
        cast(float | None, options.get("idle_timeout_seconds")),
        target.idle_timeout_seconds,
        code="invalid_idle_timeout",
        target_id=target_id,
    )
    request = SendMessageRequest(
        message=build_user_message(
            message_id=request_id,
            capability=remote_capability,
            text=options.get("text"),
            data=options.get("data"),
            task_id=options.get("task_id"),
            context_id=options.get("context_id"),
        )
    )
    request.metadata["skill_id"] = remote_capability
    return total, idle, request, remote_capability


async def _fetch_execution_card(
    state: _ExecutionState,
    target_id: str,
) -> AgentCard:
    """Fetch the reduced card through the selected resource owner."""
    return await state.card_fetcher(target_id, **state.card_kwargs)


async def _open_execution(
    state: _ExecutionState,
    target_id: str,
) -> tuple[Any, httpx.AsyncClient, AsyncIterator[Any]]:
    """Fetch a reduced card and create the official SDK client/iterator."""
    card = await _fetch_execution_card(state, target_id)
    http_client = state.client_factory(target_id, **state.factory_kwargs)
    try:
        sdk_client = state.sdk_factory(
            ClientConfig(streaming=True, httpx_client=http_client)
        ).create(card)
        iterator = sdk_client.send_message(state.request)
    except Exception:
        await _close_client(http_client)
        raise
    return sdk_client, http_client, iterator


async def _iterate_execution(
    state: _ExecutionState,
    iterator: AsyncIterator[Any],
    target_id: str,
    deadline: float,
) -> AsyncIterator[ExternalA2AEvent]:
    """Apply idle/total deadlines and map each SDK stream response."""
    loop = asyncio.get_running_loop()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise InteropA2AClientError("total_timeout", target_id)
        try:
            response = await asyncio.wait_for(
                anext(iterator),
                timeout=min(state.idle, remaining),
            )
        except TimeoutError:
            code = (
                "total_timeout" if loop.time() >= deadline else "idle_timeout"
            )
            raise InteropA2AClientError(code, target_id) from None
        try:
            event = map_stream_response(
                response,
                target_id=target_id,
                capability=state.remote_capability,
            )
        except A2AMappingError as exc:
            raise InteropA2AClientError(exc.code, target_id) from None
        yield event
        if event.terminal:
            return


async def _stream_execution(
    state: _ExecutionState,
    target_id: str,
) -> AsyncIterator[ExternalA2AEvent]:
    """Run one bounded SDK stream and enforce ordered cleanup."""
    if state.interop_runtime is not None:
        async with asyncio.timeout(state.total):
            card = await _fetch_execution_card(state, target_id)

            async def stream_with_client(
                client: httpx.AsyncClient,
            ) -> AsyncIterator[ExternalA2AEvent]:
                """Stream through one runtime-owned HTTP client."""
                iterator: AsyncIterator[Any] | None = None
                try:
                    sdk_client = state.sdk_factory(
                        ClientConfig(streaming=True, httpx_client=client)
                    ).create(card)
                    iterator = sdk_client.send_message(state.request)
                    deadline = asyncio.get_running_loop().time() + state.total
                    async for event in _iterate_execution(
                        state,
                        iterator,
                        target_id,
                        deadline,
                    ):
                        yield event
                finally:
                    await _close_iterator(iterator)

            async for event in state.interop_runtime.stream_http(
                target_id,
                stream_with_client,
            ):
                yield event
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + state.total
    sdk_client: Any | None = None
    http_client: httpx.AsyncClient | None = None
    iterator: AsyncIterator[Any] | None = None
    try:
        async with asyncio.timeout(state.total):
            sdk_client, http_client, iterator = await _open_execution(
                state,
                target_id,
            )
            async for event in _iterate_execution(
                state,
                iterator,
                target_id,
                deadline,
            ):
                yield event
    finally:
        await _close_iterator(iterator)
        if sdk_client is not None:
            await _close_client(sdk_client)
        else:
            await _close_client(http_client)


async def send_external_a2a_task(
    target_id: str,
    capability_id: str,
    *,
    registry: InteropRegistry,
    **options: Any,
) -> AsyncIterator[ExternalA2AEvent]:
    """Send one text/data message and stream normalized A2A events."""
    audit_sink = cast(AuditSink | None, options.get("audit_sink"))
    event_sink = cast(AuditSink | None, options.get("event_sink"))
    if audit_sink is not None and event_sink is not None:
        raise InteropA2AClientError("duplicate_audit_sink", target_id)
    sink = audit_sink or event_sink
    started = monotonic()
    request_id = IdFactory().new_id("a2a", target_id)
    audit_context = _AuditContext(
        request_id,
        _audit_label(target_id, _TARGET_ID),
        _audit_label(capability_id, _CAPABILITY_ID),
        started,
        sink,
    )
    terminal_status: str | None = None
    error_code: str | None = None
    _audit_event(audit_context, None)
    try:
        state = _prepare_execution(
            target_id,
            capability_id,
            registry,
            options,
            request_id,
        )
        async for event in _stream_execution(state, target_id):
            if event.terminal:
                terminal_status = event.state or event.kind
            yield event
        if terminal_status is None:
            terminal_status = "stream_end"
    except InteropA2AClientError as exc:
        error_code = exc.code
        raise
    except InteropA2AError as exc:
        error_code = exc.code
        raise InteropA2AClientError(exc.code, target_id) from None
    except A2AMappingError as exc:
        error_code = exc.code
        raise InteropA2AClientError(exc.code, target_id) from None
    except asyncio.CancelledError:
        error_code = "cancelled"
        raise
    except TimeoutError:
        error_code = "total_timeout"
        raise InteropA2AClientError("total_timeout", target_id) from None
    except (
        httpx.HTTPError,
        InteropHTTPError,
        OSError,
        RuntimeError,
        ValueError,
    ):
        error_code = "transport_error"
        raise InteropA2AClientError("transport_error", target_id) from None
    except Exception:
        error_code = "transport_error"
        raise InteropA2AClientError("transport_error", target_id) from None
    finally:
        _audit_event(audit_context, terminal_status, error_code)


stream_external_a2a_task = send_external_a2a_task
send_external_a2a = send_external_a2a_task
stream_external_a2a = send_external_a2a_task


__all__ = [
    "A2AClientError",
    "AuditSink",
    "InteropA2AClientError",
    "send_external_a2a",
    "send_external_a2a_task",
    "stream_external_a2a",
    "stream_external_a2a_task",
]
