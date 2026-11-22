"""Phase 3 tools: validate, diff, approve, publish. Source: docs/interfaces/mcp-tools.md
#Review & validation / #Publish, docs/use-cases/05-publish.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from asogenic import context
from asogenic.errors import ASOgenicError, asc_state_error, validation_error
from asogenic.logging_config import get_logger
from asogenic.models.metadata import (
    FIELD_LIMITS,
    TRANSLATABLE,
    LocaleState,
    MetadataField,
    MetadataLocalization,
    PublishScope,
    measured_length,
)

logger = get_logger(__name__)


def _safe_error_message(exc: Exception) -> str:
    """Never surface a raw exception's text to the MCP caller — an ASOgenicError already carries a
    clean, typed message (str(exc) is "[category:code] message"); anything else (httpx transport
    errors, etc.) might embed request/response internals, so log the full detail server-side and
    return a generic summary instead."""
    if isinstance(exc, ASOgenicError):
        return str(exc)
    logger.warning("unexpected error during ASC write", exc_info=exc)
    return "unexpected error contacting App Store Connect (see server logs)"

_DUPLICATE_LOCALE_CODE = "ENTITY_ERROR.ATTRIBUTE.INVALID.DUPLICATE"


def _is_duplicate_locale_error(exc: Exception) -> bool:
    """True when ASC rejected a create because a localization for this locale already exists —
    confirmed live: a create that times out client-side (or is retried after an ambiguous batch
    failure) can still land on ASC's side, so the next create attempt for the same locale gets
    this exact code back. Recoverable by looking up the real id and switching to update."""
    return isinstance(exc, ASOgenicError) and exc.code == _DUPLICATE_LOCALE_CODE


_FIELD_ATTR = {
    MetadataField.NAME: "name",
    MetadataField.SUBTITLE: "subtitle",
    MetadataField.DESCRIPTION: "description",
    MetadataField.KEYWORDS: "keywords",
    MetadataField.PROMOTIONAL_TEXT: "promotional_text",
    MetadataField.WHATS_NEW: "whats_new",
}


def _record_from_dict(data: dict[str, Any]) -> MetadataLocalization:
    return MetadataLocalization(**{k: v for k, v in data.items() if k in _FIELD_ATTR.values() or k.endswith("_url")})


_ALL_CONTENT_ATTRS = sorted(set(_FIELD_ATTR.values()) | {"marketing_url", "support_url", "privacy_policy_url"})


def _content_hash(content: MetadataLocalization, *, scope: PublishScope) -> str:
    """Idempotency key for one locale's publish: identical field content plus scope must
    reproduce the same hash, so a retried asogenic_publish call for content already fully written
    can be recognized as a no-op instead of double-submitting to ASC."""
    payload = {attr: getattr(content, attr) for attr in _ALL_CONTENT_ATTRS}
    payload["__scope__"] = scope.value
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_record(*, record: dict[str, Any]) -> dict[str, Any]:
    """{fields_over_limit: [{field, limit, measured}]}"""
    localization = _record_from_dict(record)
    over_limit = []
    for field in TRANSLATABLE:
        attr = _FIELD_ATTR[field]
        value = getattr(localization, attr)
        measured = measured_length(field, value)
        limit = FIELD_LIMITS[field].limit
        if measured > limit:
            over_limit.append({"field": field.value, "limit": limit, "measured": measured})
    return {"fields_over_limit": over_limit}


def diff_locale(*, generated: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """Field-by-field {before, after} for every field that differs."""
    generated_loc = _record_from_dict(generated)
    existing_loc = _record_from_dict(existing) if existing else MetadataLocalization()

    all_attrs = set(_FIELD_ATTR.values()) | {"marketing_url", "support_url", "privacy_policy_url"}
    diff: dict[str, dict[str, str]] = {}
    for attr in all_attrs:
        before = getattr(existing_loc, attr)
        after = getattr(generated_loc, attr)
        if before != after:
            diff[attr] = {"before": before, "after": after}
    return diff


def approve_locale(*, session_key: str, locale_id: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
    """`content`, if given, replaces the record's generated_content before approving — this is how
    the client (which holds the AI-generated/edited text; the server never does) hands its final
    per-locale content back before the approval gate. Per docs/use-cases/04-review-and-validation.md
    the gate requires no field over its limit and a non-empty name."""
    session = context.SESSION_STORE.get(session_key)
    if session is None:
        raise asc_state_error(f"no session '{session_key}'", code="NOT_FOUND")
    record = session.get(locale_id)
    if record is None:
        raise asc_state_error(
            f"no fetched/generated record for locale '{locale_id}' in session '{session_key}'",
            code="NOT_FOUND",
        )
    if content is not None:
        record.generated_content = _record_from_dict(content)
    if not record.generated_content.name:
        raise asc_state_error(f"locale '{locale_id}' has an empty name — cannot approve", code="STATE_ERROR")
    over_limit = record.generated_content.fields_over_limit
    if over_limit:
        raise asc_state_error(
            f"locale '{locale_id}' has fields over their limit: {', '.join(f.value for f in over_limit)}",
            code="STATE_ERROR",
        )
    session.approve(locale_id)
    context.SESSION_STORE.flush(session_key)
    return {"state": "approved"}


def _write_with_isolation(
    payload: dict[str, str], do_call: Any
) -> tuple[list[str], dict[str, str]]:
    """Try writing every field in `payload` in one call; if that fails, retry field-by-field so
    one invalid field (e.g. `whats_new` on a version that's never shipped, which ASC rejects
    with STATE_ERROR right up until the first release exists) doesn't take every other,
    perfectly valid field in the same batch down with it — confirmed live: a batched PATCH
    with description/keywords/promotional_text/whats_new failed all four fields on a
    never-shipped app, even though only whats_new was actually invalid; dropping whats_new and
    resubmitting the other three succeeded. Returns (succeeded_attrs, {failed_attr: error})."""
    try:
        do_call(payload)
        return list(payload), {}
    except Exception as exc:  # noqa: BLE001 - isolate per-field below, only escalate if every field fails
        succeeded: list[str] = []
        failed: dict[str, str] = {}
        for attr, value in payload.items():
            try:
                do_call({attr: value})
                succeeded.append(attr)
            except Exception as inner_exc:  # noqa: BLE001 - reported per-field, not raised
                failed[attr] = _safe_error_message(inner_exc)
        if not succeeded and not failed:
            # payload was non-empty but the loop above didn't run (shouldn't happen) — surface
            # the original batch error rather than silently reporting nothing.
            failed = {attr: _safe_error_message(exc) for attr in payload}
        return succeeded, failed


def _publish_one_locale(
    client: Any, resolved: Any, record: Any, *, scope: PublishScope
) -> list[dict[str, Any]]:
    """Publish one approved LocalizationRecord's non-empty, in-scope, in-limit fields.
    Returns per-field results; does not mutate record.state (the caller decides published vs
    approved based on whether every field was written)."""
    content = record.generated_content
    limited_fields = scope.limited_fields

    results: list[dict[str, Any]] = []

    # App-level fields (appInfoLocalizations): name, subtitle, privacy URL — always publishable.
    app_level_attrs = {MetadataField.NAME: "name", MetadataField.SUBTITLE: "subtitle"}
    app_level_payload = {}
    for field, attr in app_level_attrs.items():
        if limited_fields is not None and field not in limited_fields:
            continue
        value = getattr(content, attr)
        if value:
            app_level_payload[attr] = value
    # privacy_policy_url isn't a MetadataField (it's not AI-generated/translatable, and isn't
    # scope-limited), but it lives on the same appInfoLocalizations resource as name/subtitle —
    # this used to be read (fetch_source, diff_locale) but never actually written anywhere,
    # which left asogenic_get_release_readiness's own "privacy_policy_url is not set" blocker
    # permanently unfixable through any tool this server offers.
    if content.privacy_policy_url:
        app_level_payload["privacy_policy_url"] = content.privacy_policy_url

    if app_level_payload:
        had_app_info_id = record.app_info_localization_id is not None
        recovered_app_info = {"used": False}

        def _write_app_level(payload: dict[str, str]) -> None:
            if record.app_info_localization_id:
                client.update_app_info_localization(record.app_info_localization_id, **payload)
                return
            try:
                new_id = client.create_app_info_localization(
                    app_info_id=resolved.app_info_id, locale=record.id, **payload
                )
                record.app_info_localization_id = new_id
            except ASOgenicError as exc:
                if not _is_duplicate_locale_error(exc):
                    raise
                existing = client.list_app_info_localizations(resolved.app_info_id).get(record.id)
                if existing is None or existing.app_info_localization_id is None:
                    raise
                record.app_info_localization_id = existing.app_info_localization_id
                recovered_app_info["used"] = True
                client.update_app_info_localization(record.app_info_localization_id, **payload)

        succeeded, failed = _write_with_isolation(app_level_payload, _write_app_level)
        method = "patched" if had_app_info_id or recovered_app_info["used"] else "posted"
        for attr in succeeded:
            results.append({"locale": record.id, "field": attr, "result": method})
        for attr, error in failed.items():
            results.append({"locale": record.id, "field": attr, "result": "failed", "error": error})

    # Version-level fields (appStoreVersionLocalizations): description/keywords/whats_new need
    # an editable version; promotional_text/support_url/marketing_url only need a version to
    # exist. support_url and marketing_url aren't MetadataFields (not AI-generated/translatable,
    # like privacy_policy_url) but live on this same resource — confirmed live against a real
    # submission attempt that support_url specifically was readable but never actually written
    # by this function, the same bug class privacy_policy_url had on appInfoLocalizations.
    version_required = {MetadataField.DESCRIPTION: "description", MetadataField.KEYWORDS: "keywords", MetadataField.WHATS_NEW: "whats_new"}
    promo_field = {MetadataField.PROMOTIONAL_TEXT: "promotional_text"}

    version_payload = {}
    for field, attr in {**version_required, **promo_field}.items():
        if limited_fields is not None and field not in limited_fields:
            continue
        value = getattr(content, attr)
        if not value:
            continue
        if field in version_required and not resolved.is_editable:
            results.append({"locale": record.id, "field": attr, "result": "skipped", "error": "version not editable"})
            continue
        if field in promo_field and resolved.version_id is None:
            results.append({"locale": record.id, "field": attr, "result": "skipped", "error": "no version exists"})
            continue
        version_payload[attr] = value
    for attr in ("support_url", "marketing_url"):
        value = getattr(content, attr)
        if not value:
            continue
        if resolved.version_id is None:
            results.append({"locale": record.id, "field": attr, "result": "skipped", "error": "no version exists"})
            continue
        version_payload[attr] = value

    if version_payload:
        if resolved.version_id is None:
            for attr in version_payload:
                results.append({"locale": record.id, "field": attr, "result": "skipped", "error": "no version exists"})
        else:
            had_version_loc_id = record.version_localization_id is not None
            recovered_version = {"used": False}

            def _write_version_level(payload: dict[str, str]) -> None:
                if record.version_localization_id:
                    client.update_app_store_version_localization(record.version_localization_id, **payload)
                    return
                try:
                    new_id = client.create_app_store_version_localization(
                        version_id=resolved.version_id, locale=record.id, **payload
                    )
                    record.version_localization_id = new_id
                except ASOgenicError as exc:
                    if not _is_duplicate_locale_error(exc):
                        raise
                    existing = client.list_app_store_version_localizations(resolved.version_id).get(record.id)
                    if existing is None or existing.version_localization_id is None:
                        raise
                    record.version_localization_id = existing.version_localization_id
                    recovered_version["used"] = True
                    client.update_app_store_version_localization(record.version_localization_id, **payload)

            succeeded, failed = _write_with_isolation(version_payload, _write_version_level)
            method = "patched" if had_version_loc_id or recovered_version["used"] else "posted"
            for attr in succeeded:
                results.append({"locale": record.id, "field": attr, "result": method})
            for attr, error in failed.items():
                results.append({"locale": record.id, "field": attr, "result": "failed", "error": error})

    return results


def publish(*, session_key: str, app_id: str, platform: str, locale_ids: list[str], scope: str = "all") -> dict[str, Any]:
    """POST/PATCH approved, non-empty, in-scope, in-limit fields for each named locale.
    Per docs/use-cases/05-publish.md: over-limit records are excluded before the request; a
    record is 'published' only once every non-empty field was actually written."""
    try:
        publish_scope = PublishScope(scope)
    except ValueError as exc:
        valid = ", ".join(s.value for s in PublishScope)
        raise validation_error(
            f"unknown publish scope '{scope}' (expected one of: {valid})", field_name="scope"
        ) from exc
    session = context.SESSION_STORE.get(session_key)
    if session is None:
        raise asc_state_error(f"no session '{session_key}'", code="NOT_FOUND")

    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)

    all_results: list[dict[str, Any]] = []
    for locale_id in locale_ids:
        record = session.get(locale_id)
        if record is None:
            all_results.append({"locale": locale_id, "field": None, "result": "skipped", "error": "not found in session"})
            continue
        if not record.is_approved:
            all_results.append({"locale": locale_id, "field": None, "result": "skipped", "error": "not approved"})
            continue

        over_limit = record.generated_content.fields_over_limit
        in_scope_over_limit = over_limit if publish_scope.limited_fields is None else over_limit & publish_scope.limited_fields
        if in_scope_over_limit:
            all_results.append(
                {
                    "locale": locale_id, "field": None, "result": "skipped",
                    "error": f"over limit: {', '.join(f.value for f in in_scope_over_limit)}",
                }
            )
            continue

        content_hash = _content_hash(record.generated_content, scope=publish_scope)
        if session.already_published(locale_id, content_hash):
            logger.info("publish: idempotent no-op for locale=%s (identical content already published)", locale_id)
            all_results.append(
                {
                    "locale": locale_id, "field": None, "result": "already_published",
                    "note": "identical content already published in this session; not resubmitted",
                }
            )
            continue

        field_results = _publish_one_locale(client, resolved, record, scope=publish_scope)
        all_results.extend(field_results)

        is_fully_published = field_results and all(r["result"] in ("posted", "patched") for r in field_results)
        if is_fully_published:
            record.state = LocaleState.PUBLISHED
            session.mark_published(locale_id, content_hash)
            context.SESSION_STORE.flush(session_key)

    return {"results": all_results}
