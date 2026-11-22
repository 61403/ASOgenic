"""Central logging setup and the tool-call logging decorator.

MCP over stdio uses stdout as the protocol channel — anything printed there corrupts the
JSON-RPC stream. All logging in this process must go to stderr, which is what
`logging.StreamHandler()` does by default.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_CONFIGURED = False
_METRICS_LOCK = threading.Lock()
_TOOL_CALLS: dict[tuple[str, str], int] = {}
_TOOL_DURATION_MS: dict[str, int] = {}
_TOOL_DURATION_COUNT: dict[str, int] = {}

_SECRET_KEY_MARKERS = ("pem", "secret", "token", "password", "authorization")
_AUDIT_SAFE_KEYS = frozenset(
    {
        "app_id", "platform", "locale", "session_key", "subscription_id", "group_id",
        "product_id", "version_id", "build_id", "limit", "cursor", "category",
        "workflow", "blocked_operation", "force",
    }
)


def configure_logging() -> None:
    """Idempotent: safe to call from server startup and from tests."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("ASOGENIC_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def redact(value: Any) -> Any:
    """Best-effort redaction for logging: secret-shaped values never reach a log line."""
    if isinstance(value, str) and len(value) > 120:
        return value[:120] + f"...(truncated, {len(value)} chars)"
    if isinstance(value, dict):
        return {k: "***redacted***" if _looks_secret(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return f"<list len={len(value)}>" if len(value) > 5 else [redact(v) for v in value]
    return value


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def redact_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: "***redacted***" if _looks_secret(k) else redact(v) for k, v in kwargs.items()}


def audit_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Keep durable audit rows to a small allowlist of operational identifiers.

    Tool arguments can contain customer copy, review text, URLs, emails, or other personal data;
    logging redaction is not a data-retention policy, so arbitrary arguments never enter the audit
    database.
    """
    return {key: redact(kwargs[key]) for key in _AUDIT_SAFE_KEYS if key in kwargs}


def audit_error_detail(exc: BaseException) -> str:
    """Return a short, operator-useful error description without retaining a traceback or
    arbitrary request payload. Exception messages are still redacted and capped because some
    upstream SDKs echo request data in their errors."""
    message = str(exc).strip() or "no message"
    # Upstream SDK errors occasionally echo a header or credential field. Redact common
    # secret-shaped assignments before the detail becomes durable and operator-visible.
    message = re.sub(
        r"(?i)\b(authorization|token|password|secret|api[_-]?key|private[_-]?key|pem)\b\s*[:=]\s*([^\s,;]+)",
        r"\1=[redacted]",
        message,
    )
    return str(redact(f"{exc.__class__.__name__}: {message}"))[:500]


_tool_logger = get_logger("asogenic.tools")


def _record_tool_metric(tool_name: str, outcome: str, duration_ms: int) -> None:
    with _METRICS_LOCK:
        key = (tool_name, outcome)
        _TOOL_CALLS[key] = _TOOL_CALLS.get(key, 0) + 1
        _TOOL_DURATION_MS[tool_name] = _TOOL_DURATION_MS.get(tool_name, 0) + duration_ms
        _TOOL_DURATION_COUNT[tool_name] = _TOOL_DURATION_COUNT.get(tool_name, 0) + 1


def prometheus_metrics() -> str:
    """Return low-cardinality process metrics in Prometheus text format.

    Tenant IDs and request arguments are intentionally excluded. The endpoint is protected by
    the admin bearer in hosted mode; this data is for operational monitoring, not tenant usage.
    """
    with _METRICS_LOCK:
        calls = dict(_TOOL_CALLS)
        duration = dict(_TOOL_DURATION_MS)
        counts = dict(_TOOL_DURATION_COUNT)
    lines = [
        "# HELP asogenic_tool_calls_total MCP tool calls by tool and outcome.",
        "# TYPE asogenic_tool_calls_total counter",
    ]
    for (tool, outcome), value in sorted(calls.items()):
        lines.append(f'asogenic_tool_calls_total{{tool="{tool}",outcome="{outcome}"}} {value}')
    lines.extend([
        "# HELP asogenic_tool_call_duration_milliseconds_sum MCP tool-call duration sum.",
        "# TYPE asogenic_tool_call_duration_milliseconds_sum counter",
    ])
    for tool, value in sorted(duration.items()):
        lines.append(f'asogenic_tool_call_duration_milliseconds_sum{{tool="{tool}"}} {value}')
    lines.extend([
        "# HELP asogenic_tool_call_duration_milliseconds_count MCP tool-call duration count.",
        "# TYPE asogenic_tool_call_duration_milliseconds_count counter",
    ])
    for tool, value in sorted(counts.items()):
        lines.append(f'asogenic_tool_call_duration_milliseconds_count{{tool="{tool}"}} {value}')
    return "\n".join(lines) + "\n"


def _persist_audit(
    tool_name: str,
    outcome: str,
    kwargs: dict[str, Any],
    *,
    quota_exempt: bool,
    reservation_id: int | None = None,
    started_at: str | None = None,
    duration_ms: int | None = None,
    error_category: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    """Best-effort durable audit row for the calling tenant. A no-op for local/stdio (see
    tenancy/storage.py's record_audit docstring) and never allowed to break a tool call — a
    storage hiccup here becomes a stderr warning, not a failed request. Imports asogenic.context
    lazily to avoid a module-load-order dependency between logging_config and context.
    quota_exempt tools still get an activity-log row (so a tenant's own history is complete) but
    it's marked as not counting toward their quota — otherwise checking usage would consume it."""
    try:
        from asogenic import context

        tenant_id = context.resolve_tenant_id()
        if tenant_id == context.LOCAL_TENANT_ID:
            return
        summary = json.dumps(audit_kwargs(kwargs), default=str)[:500]
        from datetime import UTC, datetime

        finished_at = datetime.now(UTC).isoformat()
        if reservation_id is not None:
            context.get_tenant_store().complete_audit(
                reservation_id, outcome, summary, finished_at=finished_at, duration_ms=duration_ms,
                error_category=error_category, error_code=error_code, error_detail=error_detail,
            )
        else:
            context.get_tenant_store().record_audit(
                tenant_id, tool_name, outcome, summary, counts_toward_quota=not quota_exempt,
                started_at=started_at, finished_at=finished_at, duration_ms=duration_ms,
                error_category=error_category, error_code=error_code, error_detail=error_detail,
            )
    except Exception:
        _tool_logger.warning("audit_log persist failed tool=%s", tool_name, exc_info=True)


def _rate_limit_windows() -> list[tuple[str, timedelta, int]]:
    """The (name, window, limit) burst caps to enforce, newest-tightest first. Reads env
    overrides once per call (cheap; keeps tests and ops able to tune without a restart dance).
    A limit of 0 disables that window."""
    from datetime import timedelta

    from asogenic import context

    if context.resolve_tenant_id() == context.LOCAL_TENANT_ID:
        return []
    from asogenic.tenancy.storage import RATE_LIMIT_PER_HOUR, RATE_LIMIT_PER_MINUTE

    per_min = int(os.environ.get("ASOGENIC_RATE_LIMIT_PER_MINUTE", RATE_LIMIT_PER_MINUTE))
    per_hour = int(os.environ.get("ASOGENIC_RATE_LIMIT_PER_HOUR", RATE_LIMIT_PER_HOUR))
    windows: list[tuple[str, timedelta, int]] = []
    if per_min > 0:
        windows.append(("minute", timedelta(minutes=1), per_min))
    if per_hour > 0:
        windows.append(("hour", timedelta(hours=1), per_hour))
    return windows


def _record_limit_rejection(tool_name: str, category: str, code: str) -> None:
    """Record a rejected call as operational activity without consuming quota."""
    try:
        from datetime import UTC, datetime

        from asogenic import context

        tenant_id = context.resolve_tenant_id()
        if tenant_id != context.LOCAL_TENANT_ID:
            now = datetime.now(UTC).isoformat()
            context.get_tenant_store().record_audit(
                tenant_id, tool_name, "rejected", "{}", counts_toward_quota=False,
                started_at=now, finished_at=now, duration_ms=0,
                error_category=category, error_code=code,
                error_detail=(
                    "The account quota was exceeded before the tool could run."
                    if category == "quota" else
                    "The account rate limit was reached before the tool could run."
                ),
            )
    except Exception:  # noqa: BLE001 - audit must never change the limit decision.
        _tool_logger.warning("audit_log rejection persist failed tool=%s", tool_name)


def _check_limits(tool_name: str) -> None:
    """Raise a typed error if the calling tenant is over any of their call limits: the
    short-window burst caps (rate_limited_error — clears in seconds/minutes) first, then the
    rolling-30-day quota (quota_exceeded_error). A no-op for local/stdio (no tenant to meter)
    and for quota-exempt tools (checking your own usage/status must still work once you're over
    a limit — see server.py's quota_exempt=True on asogenic_auth_status/asogenic_get_activity_log).
    Runs BEFORE the tool body, unlike _persist_audit which runs after — a rejected call must
    never execute."""
    from datetime import UTC, datetime

    from asogenic import context
    from asogenic.errors import quota_exceeded_error, rate_limited_error
    tenant_id = context.resolve_tenant_id()
    if tenant_id == context.LOCAL_TENANT_ID:
        return
    from asogenic.tenancy.storage import QUOTA_WINDOW

    store = context.get_tenant_store()
    now = datetime.now(UTC)

    for name, window, limit in _rate_limit_windows():
        since = now - window
        usage = store.count_recent_calls(tenant_id, since=since)
        if usage < limit:
            continue
        oldest = store.oldest_call_ts_since(tenant_id, since=since)
        retry_after_s = (
            max(1, int((datetime.fromisoformat(oldest) + window - now).total_seconds())) if oldest else 1
        )
        _tool_logger.warning(
            "rate limit exceeded tool=%s tenant=%s window=%s usage=%d limit=%d", tool_name, tenant_id, name, usage, limit
        )
        _record_limit_rejection(tool_name, "rate_limit", "rate_limit_exceeded")
        raise rate_limited_error(window=name, usage=usage, limit=limit, retry_after_s=retry_after_s)

    since = now - QUOTA_WINDOW
    limit = store.get_quota_limit(tenant_id)
    usage = store.count_recent_calls(tenant_id, since=since)
    if usage < limit:
        return
    oldest = store.oldest_call_ts_since(tenant_id, since=since)
    reset_at = (datetime.fromisoformat(oldest) + QUOTA_WINDOW).isoformat() if oldest else now.isoformat()
    _tool_logger.warning("quota exceeded tool=%s tenant=%s usage=%d limit=%d", tool_name, tenant_id, usage, limit)
    _record_limit_rejection(tool_name, "quota", "quota_exceeded")
    raise quota_exceeded_error(usage=usage, limit=limit, reset_at=reset_at)


def _enforce_limits(tool_name: str) -> int | None:
    """Check limits, then atomically reserve the quota-counting audit row."""
    from datetime import UTC, datetime

    from asogenic import context
    _check_limits(tool_name)
    tenant_id = context.resolve_tenant_id()
    if tenant_id == context.LOCAL_TENANT_ID:
        return None
    from asogenic.tenancy.storage import QUOTA_WINDOW

    now = datetime.now(UTC)
    windows = [(name, now - window, limit) for name, window, limit in _rate_limit_windows()]
    store = context.get_tenant_store()
    reservation_id = store.reserve_quota_call(
        tenant_id,
        tool_name,
        windows=windows,
        quota_since=now - QUOTA_WINDOW,
        quota_limit=store.get_quota_limit(tenant_id),
        ts=now,
    )
    if reservation_id is None:
        # Another concurrent request won the reservation race; the second check reports the
        # appropriate typed rate/quota error using the now-visible reservation.
        _check_limits(tool_name)
        raise RuntimeError("quota reservation failed after limit check")
    return reservation_id


def log_tool_call(tool_name: str, *, quota_exempt: bool = False) -> Callable[[F], F]:
    """Logs every invocation of an MCP tool: start (redacted args), outcome, and duration —
    always to stderr, and additionally to the calling tenant's durable audit_log for hosted
    tenants (see _persist_audit). Also enforces the tenant's call quota before running the tool
    body, unless quota_exempt=True (see _enforce_limits) — a tool marked exempt neither consumes
    quota nor is blocked by it, so a tenant over their limit can still see that fact. Wraps both
    sync and async tool functions; never swallows the underlying exception."""

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                reservation_id: int | None = None
                if not quota_exempt:
                    reservation_id = _enforce_limits(tool_name)
                from datetime import UTC, datetime

                started_at = datetime.now(UTC).isoformat()
                started = time.monotonic()
                _tool_logger.info("tool_call start tool=%s args=%s", tool_name, redact_kwargs(kwargs))
                try:
                    result: Awaitable[Any] = fn(*args, **kwargs)
                    value = await result
                except Exception as exc:
                    duration_ms = int((time.monotonic() - started) * 1000)
                    _record_tool_metric(tool_name, "error", duration_ms)
                    _tool_logger.exception(
                        "tool_call failed tool=%s duration_ms=%d",
                        tool_name,
                        duration_ms,
                    )
                    _persist_audit(
                        tool_name, "error", kwargs, quota_exempt=quota_exempt, reservation_id=reservation_id,
                        started_at=started_at, duration_ms=duration_ms,
                        error_category="tool_error", error_code="uncaught_exception", error_detail=audit_error_detail(exc),
                    )
                    raise
                duration_ms = int((time.monotonic() - started) * 1000)
                _record_tool_metric(tool_name, "ok", duration_ms)
                _tool_logger.info("tool_call ok tool=%s duration_ms=%d", tool_name, duration_ms)
                _persist_audit(
                    tool_name, "ok", kwargs, quota_exempt=quota_exempt, reservation_id=reservation_id,
                    started_at=started_at, duration_ms=duration_ms,
                )
                return value

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            reservation_id: int | None = None
            if not quota_exempt:
                reservation_id = _enforce_limits(tool_name)
            from datetime import UTC, datetime

            started_at = datetime.now(UTC).isoformat()
            started = time.monotonic()
            _tool_logger.info("tool_call start tool=%s args=%s", tool_name, redact_kwargs(kwargs))
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                _record_tool_metric(tool_name, "error", duration_ms)
                _tool_logger.exception(
                    "tool_call failed tool=%s duration_ms=%d",
                    tool_name,
                    duration_ms,
                )
                _persist_audit(
                    tool_name, "error", kwargs, quota_exempt=quota_exempt, reservation_id=reservation_id,
                    started_at=started_at, duration_ms=duration_ms,
                    error_category="tool_error", error_code="uncaught_exception", error_detail=audit_error_detail(exc),
                )
                raise
            duration_ms = int((time.monotonic() - started) * 1000)
            _record_tool_metric(tool_name, "ok", duration_ms)
            _tool_logger.info("tool_call ok tool=%s duration_ms=%d", tool_name, duration_ms)
            _persist_audit(
                tool_name, "ok", kwargs, quota_exempt=quota_exempt, reservation_id=reservation_id,
                started_at=started_at, duration_ms=duration_ms,
            )
            return value

        return sync_wrapper  # type: ignore[return-value]

    return decorator
