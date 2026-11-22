"""Typed error model. Source: docs/interfaces/errors.md.

The server returns these and stops — it never decides how to recover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    ASC_AUTH = "asc_auth"
    ASC_STATE = "asc_state"
    VALIDATION = "validation"
    SCREENSHOT_UPLOAD = "screenshot_upload"
    QUOTA = "quota"


@dataclass
class ASOgenicError(Exception):
    category: ErrorCategory
    code: str
    message: str
    hint: str = ""
    is_credential_error: bool = False
    is_cancellation: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.category.value}:{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "is_credential_error": self.is_credential_error,
            "is_cancellation": self.is_cancellation,
            "detail": self.detail,
        }


def asc_auth_error(message: str, *, is_credential_error: bool = True) -> ASOgenicError:
    return ASOgenicError(
        category=ErrorCategory.ASC_AUTH,
        code="ASC_AUTH_FAILED",
        message=message,
        hint="check ASC credentials",
        is_credential_error=is_credential_error,
    )


def asc_state_error(message: str, *, code: str = "STATE_ERROR", detail: dict[str, Any] | None = None) -> ASOgenicError:
    return ASOgenicError(
        category=ErrorCategory.ASC_STATE,
        code=code,
        message=message,
        hint="per-field version-gate detail",
        detail=detail or {},
    )


def validation_error(
    message: str, *, field_name: str, limit: int | None = None, code: str = "INVALID_INPUT",
    detail: dict[str, Any] | None = None,
) -> ASOgenicError:
    """`code` defaults to the generic `INVALID_INPUT` — this constructor covers every kind of bad
    input across the tool surface (empty strings, unknown enum values, malformed cursors, a
    missing prerequisite), not just a field over its length limit. Confirmed live: every one of
    this repo's ~35 call sites hit the hardcoded former default (`FIELD_OVER_LIMIT`) regardless
    of what was actually wrong, which would mislead a caller branching on `error.code` (e.g. into
    showing "shorten this field" UI for an unrelated validation failure like a bad cursor or an
    unauthenticated tenant). The one caller that has an actual over-limit case to report
    (asogenic_approve_locale, via publish.py) doesn't even go through this constructor — it uses
    asc_state_error directly. Pass `code` explicitly only for a call site that legitimately needs
    a more specific sub-code."""
    return ASOgenicError(
        category=ErrorCategory.VALIDATION,
        code=code,
        message=message,
        hint=f"the violating field is '{field_name}'" + (f", limit {limit}" if limit is not None else ""),
        detail={"field": field_name, "limit": limit, **(detail or {})},
    )


def app_privacy_unsupported_error() -> ASOgenicError:
    """Apple has never exposed App Privacy ("nutrition label") management to API-key auth at
    all — confirmed live (every `appDataUsages` path variant 404s/PATH_ERRORs) and matching
    fastlane's own documented conclusion (its privacy actions require Apple ID session auth +
    2FA and a private, undocumented Spaceship endpoint, not the public API-key-based ASC API
    this server uses). There is no fix available to this server or any other API-key-only
    client — raised in place of Apple's raw, unhelpful PATH_ERROR so every caller sees the same
    actionable message and stable `code`, not just the one that happened to read the docstring
    first."""
    return ASOgenicError(
        category=ErrorCategory.ASC_STATE,
        code="APP_PRIVACY_NO_API",
        message=(
            "App Privacy (the \"nutrition label\") cannot be read or written through the App "
            "Store Connect API with key-based auth — Apple has never exposed this endpoint to "
            "API keys, only to session-authenticated web UI access. This is a permanent Apple "
            "platform limitation, not a bug in this server or a transient failure worth "
            "retrying. Tell the user: open App Store Connect in a browser, go to this app's "
            "App Privacy tab, fill in the data-collection questionnaire, and press Publish "
            "there — this server cannot check or set it for you, and this is Apple review's "
            "most common rejection reason, so it must be done before submitting for review."
        ),
        hint="open App Store Connect web UI -> App Privacy tab; this server has no API path here",
    )


def iap_not_migrated_error() -> ASOgenicError:
    """Apple requires a one-time, irreversible per-app opt-in ("Manage In-App Purchases and
    Subscriptions via the App Store Connect API", App Store Connect web UI, Features tab)
    before any `inAppPurchasesV2`/`subscriptions` endpoint works for an app that hasn't opted
    in. Raised in place of Apple's own raw error so every caller — not just one that already
    read the tool docstring — gets the same actionable message and a stable `code` to branch
    on.

    CORRECTION (confirmed live): an earlier version of this docstring cited
    `PARAMETER_ERROR.INVALID 'inAppPurchasesV2' is not a valid type name` as *the* proof of an
    unmigrated app. That was a misdiagnosis — that exact message was this server's own bug
    (asc_client.py's list_in_app_purchases sent `fields[inAppPurchasesV2]`, but the sparse
    fieldset param must name the JSON:API resource *type* returned, `inAppPurchases`, not the
    `inAppPurchasesV2` collection path segment), confirmed by successfully creating and then
    listing an IAP on an app this error had been firing for. Now fixed at the source, but
    _reraise_if_iap_not_migrated's heuristic (a `PARAMETER_ERROR.INVALID`/`NOT_FOUND`/
    `PATH_ERROR` whose message mentions "inapppurchase") is left in place as a best-effort
    catch-all for a genuinely unmigrated app on another call path, since only the specific
    false-positive above was ever confirmed and disproven."""
    return ASOgenicError(
        category=ErrorCategory.ASC_STATE,
        code="IAP_ACCOUNT_NOT_MIGRATED",
        message=(
            "This app has never been migrated to API-based In-App Purchase management, so "
            "Apple rejects every in-app-purchase/subscription call for it. This is an "
            "account/app-level prerequisite, not a bug in this server or a transient failure "
            "worth retrying. Tell the user: open App Store Connect, go to this app's Features "
            "tab, and opt in to 'Manage In-App Purchases and Subscriptions via the App Store "
            "Connect API'. That choice is irreversible, so a human must make it — do not "
            "attempt it on the user's behalf."
        ),
        hint="open App Store Connect web UI -> Features tab -> opt in to API-based IAP management",
    )


def quota_exceeded_error(*, usage: int, limit: int, reset_at: str) -> ASOgenicError:
    return ASOgenicError(
        category=ErrorCategory.QUOTA,
        code="QUOTA_EXCEEDED",
        message=f"quota exceeded: {usage}/{limit} calls in the current 30-day window",
        hint=f"the window rolls forward continuously; the oldest call in it ages out around {reset_at}",
        detail={"usage": usage, "limit": limit, "reset_at": reset_at},
    )


def rate_limited_error(*, window: str, usage: int, limit: int, retry_after_s: int) -> ASOgenicError:
    """Short-window burst guard (per-minute / per-hour), distinct from QUOTA_EXCEEDED: this one
    clears on its own in seconds-to-minutes, so the right response is to slow down and retry,
    not to tell the user their monthly allowance is gone. `retry_after_s` is when the oldest
    call in the offending window ages out."""
    return ASOgenicError(
        category=ErrorCategory.QUOTA,
        code="RATE_LIMITED",
        message=f"rate limit exceeded: {usage}/{limit} calls in the last {window}",
        hint=f"this is a short-term burst limit, not your 30-day quota — retry in about {retry_after_s}s",
        detail={"window": window, "usage": usage, "limit": limit, "retry_after_s": retry_after_s},
    )


def api_message(errors_json: dict[str, Any] | list[Any] | None, *, status_code: int, fallback: str) -> str:
    """Parse a JSON:API `{"errors": [...]}` envelope; surface detail (fallback title), joined + deduped.

    Falls back to a status-code message when the body isn't JSON:API shaped.
    """
    items: list[dict[str, Any]] = []
    if isinstance(errors_json, dict):
        maybe = errors_json.get("errors")
        if isinstance(maybe, list):
            items = [e for e in maybe if isinstance(e, dict)]
    elif isinstance(errors_json, list):
        items = [e for e in errors_json if isinstance(e, dict)]

    if not items:
        return fallback or f"HTTP {status_code}"

    messages: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = item.get("detail") or item.get("title") or fallback or f"HTTP {status_code}"
        if text not in seen:
            seen.add(text)
            messages.append(text)
    return "; ".join(messages)


def is_credential_error(*, status_code: int | None, p8_parse_failed: bool = False) -> bool:
    """True for exactly two cases: a 401 from ASC, or a locally-unparseable .p8."""
    return status_code == 401 or p8_parse_failed
