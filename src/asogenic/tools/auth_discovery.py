"""Phase 2 tools: auth, app discovery, source fetch, field spec, locale status. Source:
docs/interfaces/mcp-tools.md #Auth & discovery / #Source fetch & guidance.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from typing import Any

from asogenic import context
from asogenic.config import ASCCredentials, load_asc_credentials
from asogenic.logging_config import get_logger
from asogenic.models.locales import APP_STORE_LOCALES
from asogenic.models.metadata import LocaleState, LocalizationRecord, MetadataLocalization
from asogenic.services.asc_auth import mint_jwt
from asogenic.session import session_key as make_session_key

logger = get_logger(__name__)


def _tenant_credentials() -> ASCCredentials | None:
    """Return only credentials permitted for the calling tenant.

    Environment credentials are local/stdio-only. Hosted requests use the encrypted credential
    shared by their dashboard owner; a deployment-wide ASC_* value must never become a default.
    """
    tenant_id = context.resolve_tenant_id()
    if tenant_id == context.LOCAL_TENANT_ID:
        return load_asc_credentials()
    stored = context.get_tenant_store().load_asc_credential(tenant_id)
    if stored is None:
        return None
    context.get_tenant_store().touch_credential_last_used(tenant_id)
    return ASCCredentials(key_id=stored.key_id, issuer_id=stored.issuer_id, p8_pem=stored.p8_pem)


def auth_status() -> dict[str, Any]:
    """{asc_configured, asc_valid, credential_source} — whether account ASC credentials are present and
    mint a valid JWT, and where they came from: `"env"`, `"vault"`, `"session"`, or `None`.
    For any hosted tenant, also
    `quota_usage` / `quota_limit` for the rolling 30-day window — this call is quota-exempt, so
    it stays visible."""
    tenant_id = context.resolve_tenant_id()
    creds = _tenant_credentials()
    if creds is not None:
        source = "env" if tenant_id == context.LOCAL_TENANT_ID else "vault"
        try:
            mint_jwt(creds)
            result: dict[str, Any] = {"asc_configured": True, "asc_valid": True, "credential_source": source}
            logger.info("auth_status: valid (source=%s key_id=%s)", source, creds.key_id)
        except Exception:
            result = {"asc_configured": True, "asc_valid": False, "credential_source": source}
            logger.warning("auth_status: %s credential invalid (key_id=%s)", source, creds.key_id, exc_info=True)
    else:
        logger.info("auth_status: no ASC credentials configured")
        result = {"asc_configured": False, "asc_valid": False, "credential_source": None}

    if tenant_id != context.LOCAL_TENANT_ID:
        if not result["asc_valid"]:
            # The account dashboard claims the verified OAuth workspace after login and owns the
            # shared Apple credential for OAuth plus all of the person's platform keys. No
            # tenant id, credential, or token appears in this browser handoff.
            from urllib.parse import urljoin

            base = os.environ.get("ASOGENIC_DASHBOARD_BASE_URL", "https://asogenic.com").rstrip("/")
            result["next_action"] = {
                "action": "connect_app_store",
                "url": urljoin(base + "/", "dashboard"),
                "message": (
                    "Open this URL in a browser. Sign in to ASOgenic, then enter your App Store Connect "
                    "Issuer ID, Key ID, and .p8 file on the App Store Connect card. Do not send the private "
                    "key through chat or an MCP tool. After saving it, retry this action."
                ),
            }
        store = context.get_tenant_store()
        # Quota is a call-volume property of the tenant and is independent of ASC credentials.
        from asogenic.tenancy.storage import QUOTA_WINDOW

        since = datetime.now(UTC) - QUOTA_WINDOW
        result["quota_usage"] = store.count_recent_calls(tenant_id, since=since)
        result["quota_limit"] = store.get_quota_limit(tenant_id)
    return result


def clear_asc_credential() -> dict[str, Any]:
    """Delete the calling hosted account's encrypted ASC credential and cached client."""
    tenant_id = _require_hosted_tenant("asogenic_clear_asc_credential")
    deleted = context.get_tenant_store().delete_asc_credential(tenant_id)
    context.clear_credential_scope_asc_clients(tenant_id)
    return {"deleted": deleted, "credential_source": None}


def credential_status() -> dict[str, Any]:
    """Return non-secret vault metadata for the calling hosted account."""
    tenant_id = _require_hosted_tenant("asogenic_get_asc_credential_status")
    meta = context.get_tenant_store().get_credential_meta(tenant_id)
    return {
        "stored": meta is not None,
        "credential_source": "vault" if meta is not None else None,
        "created_at": meta.created_at if meta is not None else None,
        "last_used_at": meta.last_used_at if meta is not None else None,
    }


def _require_hosted_tenant(tool_name: str) -> str:
    tenant_id = context.resolve_tenant_id()
    if tenant_id == context.LOCAL_TENANT_ID:
        from asogenic.errors import validation_error

        raise validation_error(
            f"no authenticated tenant on this connection — {tool_name} requires a platform API "
            "key (hosted/HTTP mode); it has no meaning for local/stdio use.",
            field_name="tenant",
        )
    return tenant_id


def rotate_api_key() -> dict[str, Any]:
    """Issues a new platform API key for the calling tenant and invalidates the prior key
    atomically. The new key is shown here exactly once; store it now. This rotates the key that
    authenticates you to this server. To replace the ASC credential used on your behalf, hosted
    clients use the authenticated dashboard; local/stdio clients replace the MCP client's local
    ASC environment values."""
    tenant_id = _require_hosted_tenant("asogenic_rotate_api_key")
    store = context.get_tenant_store()
    new_key = store.rotate_api_key(tenant_id)
    if new_key is None:  # pragma: no cover - tenant existed a moment ago to reach this call
        from asogenic.errors import asc_state_error

        raise asc_state_error("tenant no longer exists", code="NOT_FOUND")
    logger.info("rotate_api_key: issued new platform key for tenant")
    return {"api_key": new_key}


def revoke_tenant() -> dict[str, Any]:
    """Permanently deactivates the calling tenant: the platform API key used for this call stops
    working immediately (no grace window). This tenant's own activity-log history is kept (a forensic
    record of a since-revoked tenant, not something a revoke call should be able to erase) but is
    no longer reachable through asogenic_get_activity_log once the key stops authenticating. There
    is no undo: re-registering via asogenic_register_tenant creates an unrelated new tenant with a
    new tenant_id."""
    tenant_id = _require_hosted_tenant("asogenic_revoke_tenant")
    store = context.get_tenant_store()
    revoked = store.revoke_tenant(tenant_id)
    context.clear_tenant_state(tenant_id)
    logger.info("revoke_tenant: tenant revoked (already gone: %s)", not revoked)
    return {"revoked": True}


_LIST_APPS_MAX_LIMIT = 200
_LIST_APPS_DEFAULT_LIMIT = 50


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        offset = int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        from asogenic.errors import validation_error

        raise validation_error(f"invalid cursor: {cursor!r}", field_name="cursor") from exc
    if offset < 0:
        from asogenic.errors import validation_error

        raise validation_error(f"invalid cursor: {cursor!r}", field_name="cursor")
    return offset


def list_apps(*, cursor: str | None = None, limit: int = _LIST_APPS_DEFAULT_LIMIT) -> dict[str, Any]:
    """One page of apps plus an opaque `next_cursor` (null on the last page). ASC itself is
    fetched in full (bounded at MAX_PAGES/PAGE_LIMIT in ASCClient) and paged here at the MCP
    layer — cheap and correct at the account sizes this tool sees in practice, and avoids
    threading a second, ASC-native cursor format through this tool's contract."""
    if limit <= 0 or limit > _LIST_APPS_MAX_LIMIT:
        from asogenic.errors import validation_error

        raise validation_error(
            f"limit must be between 1 and {_LIST_APPS_MAX_LIMIT}, got {limit}", field_name="limit"
        )
    offset = _decode_cursor(cursor) if cursor else 0
    client = context.get_asc_client()
    all_apps = client.list_apps()
    page = all_apps[offset : offset + limit]
    next_offset = offset + limit
    next_cursor = _encode_cursor(next_offset) if next_offset < len(all_apps) else None
    return {
        "apps": [
            {
                "bundle_id": app.bundle_id,
                "name": app.name,
                "platforms": app.platforms,
                "primary_locale": app.primary_locale,
            }
            for app in page
        ],
        "next_cursor": next_cursor,
    }


_ACTIVITY_LOG_MAX_LIMIT = 200
_ACTIVITY_LOG_DEFAULT_LIMIT = 50


def get_activity_log(*, cursor: str | None = None, limit: int = _ACTIVITY_LOG_DEFAULT_LIMIT) -> dict[str, Any]:
    """This tenant's own persisted tool-call history, newest first — never another tenant's; the
    tenant_id is always resolved from the authenticated connection, never accepted as a
    parameter. Requires an authenticated tenant (hosted/HTTP mode): local/stdio runs are
    intentionally never persisted (see tenancy/storage.py's record_audit), so this raises a
    validation error there rather than silently returning nothing."""
    tenant_id = context.resolve_tenant_id()
    if tenant_id == context.LOCAL_TENANT_ID:
        from asogenic.errors import validation_error

        raise validation_error(
            "no authenticated tenant on this connection — asogenic_get_activity_log requires a "
            "platform API key (hosted/HTTP mode); local/stdio tool calls are logged to stderr "
            "only and are not persisted.",
            field_name="tenant",
        )
    if limit <= 0 or limit > _ACTIVITY_LOG_MAX_LIMIT:
        from asogenic.errors import validation_error

        raise validation_error(
            f"limit must be between 1 and {_ACTIVITY_LOG_MAX_LIMIT}, got {limit}", field_name="limit"
        )
    before_id = _decode_cursor(cursor) if cursor else None
    entries, next_before_id = context.get_tenant_store().list_audit_log(
        tenant_id, limit=limit, before_id=before_id
    )
    return {
        "entries": [
            {"ts": e.ts, "tool_name": e.tool_name, "outcome": e.outcome, "summary": e.summary}
            for e in entries
        ],
        "next_cursor": _encode_cursor(next_before_id) if next_before_id is not None else None,
    }


def resolve_app(*, app_id: str, platform: str) -> dict[str, Any]:
    """`app_id` is the app's bundle ID (see asogenic_list_apps). Returns `session_key`, opaque to
    the client — pass it verbatim to asogenic_get_locale_status / asogenic_approve_locale /
    asogenic_publish; do not construct it yourself."""
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    return {
        "app_info_id": resolved.app_info_id,
        "version_id": resolved.version_id,
        "is_editable": resolved.is_editable,
        "version_state": resolved.version_state,
        "session_key": make_session_key(bundle_id=app_id, platform=platform, version_id=resolved.version_id),
    }


def list_locales(*, app_id: str, platform: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    locale_ids = client.list_locales(resolved.app_info_id, resolved.version_id)
    return {
        "locales": [
            {
                "locale": ids.locale,
                # App-info localization is shared across platforms; platform inventory is
                # populated only when the requested platform has its version localization.
                "populated": bool(ids.version_localization_id),
                "app_info_localization_id": ids.app_info_localization_id,
                "version_localization_id": ids.version_localization_id,
            }
            for ids in locale_ids.values()
        ]
    }


def fetch_source(*, app_id: str, platform: str, locale: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    content_by_locale = client.fetch_all_localizations(resolved.app_info_id, resolved.version_id)

    # The source locale is an app-level identity, not the locale requested for this
    # fetch. Prefer Apple's conventional English source when present, otherwise use a
    # stable populated locale so empty-locale fetches cannot redefine the source.
    source_locale = "en-US" if "en-US" in content_by_locale else (
        min(content_by_locale) if content_by_locale else locale
    )
    source = content_by_locale.get(locale)
    content = source.to_metadata() if source is not None else None
    localization_ids = {
        "app_info_localization_id": source.app_info_localization_id if source else None,
        "version_localization_id": source.version_localization_id if source else None,
    }

    populated_locales = sorted(loc for loc in content_by_locale if loc != source_locale)
    all_locale_ids = {loc.id for loc in APP_STORE_LOCALES}
    eligible_empty_locales = sorted(all_locale_ids - set(content_by_locale.keys()) - {source_locale})

    version_required = ["description", "keywords", "whats_new", "marketing_url", "support_url"]
    always_publishable = ["name", "subtitle", "privacy_policy_url"]
    promo_publishable = resolved.version_id is not None
    publishable_now = list(always_publishable)
    if promo_publishable:
        publishable_now.append("promotional_text")
    if resolved.is_editable:
        publishable_now.extend(version_required)
        needs_new_version: list[str] = []
    else:
        needs_new_version = list(version_required)

    next_guidance = {
        "version_state": resolved.version_state,
        "editable": resolved.is_editable,
        "publishable_now": publishable_now,
        "needs_new_version": needs_new_version,
        "promo_text_publishable": promo_publishable,
        "source_locale": source_locale,
        "populated_locales": populated_locales,
        "eligible_empty_locales": eligible_empty_locales,
    }

    display_name = next((loc.display_name for loc in APP_STORE_LOCALES if loc.id == locale), locale)
    session_key = make_session_key(bundle_id=app_id, platform=platform, version_id=resolved.version_id)
    session = context.SESSION_STORE.get_or_create(session_key)
    session.put(
        LocalizationRecord(
            id=locale,
            display_name=display_name,
            existing_content=content,
            app_info_localization_id=localization_ids["app_info_localization_id"],
            version_localization_id=localization_ids["version_localization_id"],
            generated_content=content if content is not None else MetadataLocalization(),
            state=LocaleState.POPULATED if content is not None else LocaleState.EMPTY,
        )
    )
    context.SESSION_STORE.flush(session_key)

    return {
        "content": content.__dict__ if content is not None else None,
        "localization_ids": localization_ids,
        "next": next_guidance,
        "session_key": session_key,
    }


def get_locale_status(*, session_key: str) -> dict[str, Any]:
    session = context.SESSION_STORE.get(session_key)
    if session is None:
        return {}
    return {
        locale_id: {"state": record.state.value, "is_approved": record.is_approved}
        for locale_id, record in session.records.items()
    }
