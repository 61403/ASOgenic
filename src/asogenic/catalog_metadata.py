"""Public-safe App Store listing and locale workflow tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import auth_discovery, field_spec
from asogenic.tools import publish as publish_tools

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_metadata_catalog(server: FastMCP) -> None:
    """Register the App Store listing and locale workflow tool group."""

    @server.tool(name="asogenic_list_apps", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_apps")
    def asogenic_list_apps(cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Call this first to find the `app_id` (bundle id) every other tool needs, unless you
        already know it.

        Returns `{"apps": [{bundle_id, name, platforms, primary_locale}, ...], "next_cursor": str |
        None}`. `limit` (default 50, max 200) caps this call's page size; pass the returned
        `next_cursor` back as `cursor` to fetch the next page. `next_cursor` is null once there are
        no more apps."""
        return auth_discovery.list_apps(cursor=cursor, limit=limit)

    @server.tool(name="asogenic_resolve_app", annotations=_READ_ONLY)
    @log_tool_call("asogenic_resolve_app")
    def asogenic_resolve_app(app_id: str, platform: str) -> dict[str, Any]:
        """Resolve appInfo/version IDs and editable state for one app+platform. `app_id` is the
        app's bundle ID. Call this (or asogenic_fetch_source, which does the same resolution) before
        any tool that needs `session_key`.

        Returns `{app_info_id, version_id, is_editable, version_state, session_key}`. `version_id`
        is null if this platform has no app store version yet (a brand-new app) — most release-setup
        writes need one to exist first. `session_key` is opaque — pass it, unmodified, to
        asogenic_get_locale_status/asogenic_approve_locale/asogenic_publish for this app+platform, do
        not construct or parse it yourself."""
        return auth_discovery.resolve_app(app_id=app_id, platform=platform)

    @server.tool(name="asogenic_list_locales", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_locales")
    def asogenic_list_locales(app_id: str, platform: str) -> dict[str, Any]:
        """Locale inventory for an app+platform: which locales are populated vs empty — use this to
        decide which locales still need asogenic_fetch_source + generation + asogenic_publish.

        Returns `{"locales": [{locale, populated, app_info_localization_id,
        version_localization_id}, ...]}` — one entry per locale ASC already has a record for
        (empty/eligible locales this app has never touched aren't listed here;
        asogenic_fetch_source's `eligible_empty_locales` covers those)."""
        return auth_discovery.list_locales(app_id=app_id, platform=platform)

    @server.tool(name="asogenic_fetch_source", annotations=_READ_ONLY)
    @log_tool_call("asogenic_fetch_source")
    def asogenic_fetch_source(app_id: str, platform: str, locale: str) -> dict[str, Any]:
        """Call this first for every locale you're about to generate or edit metadata for — it seeds
        the session cache so asogenic_get_locale_status/asogenic_approve_locale/asogenic_publish work
        right after.

        Returns `{content, localization_ids, next, session_key}`:
        - `content`: the locale's current field values (name/subtitle/description/keywords/etc, or
          null if this locale has never been populated) — the baseline to generate/edit from.
        - `localization_ids`: `{app_info_localization_id, version_localization_id}`, both nullable.
        - `next`: procedural guidance — `publishable_now` (fields you can write right now),
          `needs_new_version` (fields blocked until the version is editable), `editable`,
          `version_state`, `promo_text_publishable`, `populated_locales`, `eligible_empty_locales`.
        - `session_key`: same value as asogenic_resolve_app for this app+platform; pass it to
          asogenic_approve_locale/asogenic_publish."""
        return auth_discovery.fetch_source(app_id=app_id, platform=platform, locale=locale)

    @server.tool(name="asogenic_get_field_spec", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_field_spec")
    def asogenic_get_field_spec(locale: str, fields: list[str] | None = None) -> dict[str, Any]:
        """Per-field limits, byte-vs-character measurement, generable flag, and barred-content
        rules the client's generation must obey — check this before generating name/subtitle/
        description/keywords/etc, not after (asogenic_approve_locale will reject over-limit content,
        but this tells you the limit up front instead of via a rejection round-trip).

        Returns `{locale, fields: {field_name: {limit, is_byte_limit, generable, barred_rules}}}`.
        `fields` defaults to every generable field if omitted."""
        return field_spec.get_field_spec(locale=locale, fields=fields)

    @server.tool(name="asogenic_get_locale_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_locale_status")
    def asogenic_get_locale_status(session_key: str) -> dict[str, Any]:
        """Check this to see which locales in a session are still waiting on
        asogenic_approve_locale/asogenic_publish before assuming a batch operation is done.

        Returns `{locale_id: {state, is_approved}, ...}` for every locale
        asogenic_fetch_source has touched in this session. `state` is one of `empty`, `populated`,
        `optedIn`, `approved`, `published`, `failed`. Returns `{}` if the session_key is unknown
        (e.g. never created, or from a different app+platform+version)."""
        return auth_discovery.get_locale_status(session_key=session_key)

    @server.tool(name="asogenic_validate_record", annotations=_READ_ONLY)
    @log_tool_call("asogenic_validate_record")
    def asogenic_validate_record(record: dict[str, Any]) -> dict[str, Any]:
        """Check a generated record before calling asogenic_approve_locale, which will otherwise
        reject it with the same information but no chance to fix it first.

        Returns `{"fields_over_limit": [{field, limit, measured}, ...]}` — empty list means every
        field is within limit."""
        return publish_tools.validate_record(record=record)

    @server.tool(name="asogenic_diff_locale", annotations=_READ_ONLY)
    @log_tool_call("asogenic_diff_locale")
    def asogenic_diff_locale(
        generated: dict[str, Any], existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """See exactly what a publish would change before committing to it — useful when `existing`
        (typically asogenic_fetch_source's `content`) came from a real ASC record and you want a
        human-reviewable diff, not just the final text.

        Returns `{field_name: {before, after}, ...}` — only fields that actually differ are
        included; an unchanged field is omitted entirely, not shown as before==after. Pass
        `existing=None` (or omit it) to treat every non-empty field in `generated` as new."""
        return publish_tools.diff_locale(generated=generated, existing=existing)

    @server.tool(
        name="asogenic_approve_locale",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_approve_locale")
    def asogenic_approve_locale(
        session_key: str, locale_id: str, content: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Mark a session's locale record approved, queuing it for asogenic_publish. `content`, if
        given, replaces the record's generated content first (the client's final AI-generated/edited
        text) — this is how the client hands its finished text back, the server never generates
        content itself. `locale_id` is the locale code (e.g. `"en-US"`), not a UUID, even though
        asogenic_fetch_source's response also contains `localization_ids` for the same locale.

        Returns `{"state": "approved"}` on success. Raises a typed error (not silently skipping) if
        the name is empty or any field is over its limit — check asogenic_validate_record first to
        avoid the round-trip. Re-approving an already-approved locale is a no-op — safe to call more
        than once."""
        return publish_tools.approve_locale(session_key=session_key, locale_id=locale_id, content=content)

    @server.tool(
        name="asogenic_publish",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    )
    @log_tool_call("asogenic_publish")
    def asogenic_publish(
        session_key: str, app_id: str, platform: str, locale_ids: list[str], scope: str = "all"
    ) -> dict[str, Any]:
        """POST/PATCH approved records (asogenic_approve_locale first) to ASC, per-field
        version-gated. Over-limit records are excluded before the request; a mixed batch still
        publishes its good locales. A retry with the exact same approved content for a locale that
        already published successfully is a safe no-op (short-circuits to `already_published`) — but
        this is NOT declared idempotent, because a retry after the approved content changed, or after
        a partial failure, performs a real new write rather than reliably repeating the prior one. If
        a batch write fails partway (e.g. one field is invalid), the valid fields in the same batch
        still get written — failures are isolated per field, not all-or-nothing.

        This is a direct App Store Connect write, not a transaction: ASOgenic does not retain a
        durable pre-write snapshot and cannot automatically roll a locale back. Review the diff and
        confirm the target content before calling this tool; fetch the current source again before
        manually applying a corrective publish.

        Returns `{"results": [{locale, field, result, error?}, ...]}` — one entry per field per
        locale. `result` is one of `posted`, `patched`, `already_published`, `skipped`, or `failed`;
        check every entry, not just whether the call raised, since a partial failure returns
        normally with some entries `failed` and others `posted`/`patched`. `scope` (default `"all"`)
        limits which fields get written in this call — see docs/interfaces for the valid values."""
        return publish_tools.publish(
            session_key=session_key,
            app_id=app_id,
            platform=platform,
            locale_ids=locale_ids,
            scope=scope,
        )
