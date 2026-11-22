"""Public-safe App Store release, review, pricing, and privacy tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import readiness, release_setup

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_release_catalog(server: FastMCP) -> None:
    """Register release submission, accessibility, pricing, and privacy tools."""

    @server.tool(name="asogenic_get_release_readiness", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_release_readiness", quota_exempt=True)
    def asogenic_get_release_readiness(
        app_id: str, platform: str, required_display_types: list[str] | None = None
    ) -> dict[str, Any]:
        """Call this before every asogenic_submit_for_review attempt — everything currently stopping
        (or worth checking before) submitting this app+platform for Apple review, gathered from real
        ASC state in one call instead of discovering each gap as a separate submission failure.
        Quota-exempt, same reasoning as asogenic_auth_status: checking whether you're blocked
        shouldn't itself cost you calls.

        Returns `{ready: bool, blockers: list[str], warnings: list[str]}`. `ready` is `not blockers`.
        `blockers` are things Apple's own review flow requires and this server confirmed it can
        check — each message names the tool that fixes it. `warnings` are real gaps this server
        can't verify or fill (App Privacy has no API at all; per-device-size screenshot coverage
        isn't derivable from ASC state) — read them even when `ready` is true, since
        asogenic_submit_for_review can still be rejected by Apple over something a warning already
        named."""
        return readiness.get_release_readiness(
            app_id=app_id, platform=platform, required_display_types=required_display_types
        )


    @server.tool(
        name="asogenic_submit_for_review",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    )
    @log_tool_call("asogenic_submit_for_review")
    def asogenic_submit_for_review(app_id: str, platform: str, force: bool = False) -> dict[str, Any]:
        """Submits this app version to Apple for review — the actual trigger, distinct from
        asogenic_publish (which only writes metadata fields). Runs asogenic_get_release_readiness
        first and refuses if anything is blocking, unless `force=True` (Apple will reject it anyway
        in that case; force exists for a caller that wants Apple's own error text — often more
        precise than this server's own checks — instead of this server's readiness summary).
        Resumes an already-active submission for this app+platform rather than creating a duplicate
        (Apple only allows one in flight at a time).

        Returns `{"submitted": True, "review_submission_id": str}` on success. On failure (blocked,
        or `force=True` and Apple itself rejects it), raises with Apple's associated-error detail —
        this is often more specific than asogenic_get_release_readiness's blockers (confirmed live:
        it's the only place a missing device-size screenshot or an unwritten field ever surfaced).
        To pull a submission back before Apple finishes reviewing it, use
        asogenic_cancel_review_submission."""
        return readiness.submit_for_review(app_id=app_id, platform=platform, force=force)


    @server.tool(
        name="asogenic_cancel_review_submission",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    )
    @log_tool_call("asogenic_cancel_review_submission")
    def asogenic_cancel_review_submission(app_id: str, platform: str) -> dict[str, Any]:
        """Withdraws this app+platform's active review submission — asogenic_submit_for_review's
        counterpart, for "pull this back out of Apple's queue" (e.g. a bad build got submitted, or a
        metadata mistake needs fixing before Apple looks at it).

        Returns `{"canceled": True, "review_submission_id": str}` on success. Raises NOT_FOUND if
        there's no active submission — a caller can't mistake "nothing to cancel" for success.

        Timing, confirmed live against a real submission: calling this immediately after
        asogenic_submit_for_review is accepted (Apple moves the submission to a `CANCELING` state,
        same call shape this uses), but Apple's own review can still finish first and win the race —
        in one observed run the submission still landed on `COMPLETE` seconds later despite the
        cancel call succeeding. There is no guaranteed window; call this as soon as you know you want
        to cancel, then check the outcome (e.g. via asogenic_get_release_readiness or by resolving the
        app) rather than assuming success. Once a submission reaches COMPLETE, cancellation is no
        longer possible and the only path forward is editing the version and calling
        asogenic_submit_for_review again."""
        return readiness.cancel_review_submission(app_id=app_id, platform=platform)


    @server.tool(name="asogenic_get_review_submission_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_review_submission_status")
    def asogenic_get_review_submission_status(app_id: str, platform: str) -> dict[str, Any]:
        """Read the active review submission, its Apple state, and its item ids."""
        return readiness.get_review_submission_status(app_id=app_id, platform=platform)


    @server.tool(name="asogenic_list_categories", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_categories", quota_exempt=True)
    def asogenic_list_categories() -> dict[str, Any]:
        """Apple's fixed App Store category list. Works before an app has ever shipped, unlike
        asogenic_resolve_genre (which only reads an existing public listing).

        Returns `{"categories": [{id, subcategories: [id, ...]}, ...]}` — pass an `id` from here as
        `primary_category_id`/`secondary_category_id` to asogenic_set_category."""
        return release_setup.list_categories()


    @server.tool(
        name="asogenic_set_category",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_category")
    def asogenic_set_category(
        app_id: str, platform: str, primary_category_id: str, secondary_category_id: str | None = None,
        primary_subcategory_one_id: str | None = None, primary_subcategory_two_id: str | None = None,
        secondary_subcategory_one_id: str | None = None, secondary_subcategory_two_id: str | None = None,
    ) -> dict[str, Any]:
        """Set this app's primary/secondary App Store category and optional subcategories. IDs come from
        asogenic_list_categories. Required before submission — asogenic_get_release_readiness
        blocks on this being unset.

        A category returned under `subcategories` must go in a `*_subcategory_*_id` argument, not
        `secondary_category_id`: Apple treats a secondary category as another top-level category.
        Returns the applied ids."""
        return release_setup.set_category(
            app_id=app_id, platform=platform, primary_category_id=primary_category_id,
            secondary_category_id=secondary_category_id,
            primary_subcategory_one_id=primary_subcategory_one_id,
            primary_subcategory_two_id=primary_subcategory_two_id,
            secondary_subcategory_one_id=secondary_subcategory_one_id,
            secondary_subcategory_two_id=secondary_subcategory_two_id,
        )


    @server.tool(
        name="asogenic_create_version",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_version")
    def asogenic_create_version(
        app_id: str, platform: str, version_string: str, release_type: str | None = None
    ) -> dict[str, Any]:
        """Create a new appStoreVersions record — the only way to get a fresh editable version once
        the current one has already shipped. Every other version-editing tool here (asogenic_set_*,
        asogenic_attach_build, asogenic_assemble_locale, ...) reads or revises a version that already
        exists; none of them can create the next one, and asogenic_resolve_app only ever finds
        versions that are already there.

        `version_string` is the marketing version (e.g. "1.2.0"). `release_type` is optional —
        `MANUAL` (default if omitted), `AFTER_APPROVAL`, or `SCHEDULED`.

        Raises a state error up front if this platform already has an editable version — Apple
        allows only one at a time, so check asogenic_resolve_app's `is_editable` first rather than
        calling this speculatively. Raises a validation error for an empty `version_string` or an
        unrecognized `release_type`.

        Returns `{"created": True, "version_id": str, "version_string": str}`."""
        return release_setup.create_version(
            app_id=app_id, platform=platform, version_string=version_string, release_type=release_type
        )


    @server.tool(
        name="asogenic_set_version_copyright",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_version_copyright")
    def asogenic_set_version_copyright(app_id: str, platform: str, copyright_text: str) -> dict[str, Any]:
        """Set this app store version's copyright line (e.g. "2026 Acme Inc."). Confirmed live: a
        hard requirement for review submission that nothing else in this tool surface checks or
        writes — asogenic_get_release_readiness blocks on this being unset.

        Returns `{"set": True, "copyright": str}`. Raises if `copyright_text` is blank/whitespace,
        or if there's no app store version yet to set it on."""
        return release_setup.set_version_copyright(app_id=app_id, platform=platform, copyright_text=copyright_text)


    @server.tool(
        name="asogenic_set_content_rights_declaration",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_content_rights_declaration")
    def asogenic_set_content_rights_declaration(app_id: str, declaration: str) -> dict[str, Any]:
        """Declare whether this app uses third-party content requiring rights you don't own.
        `declaration` is `DOES_NOT_USE_THIRD_PARTY_CONTENT` or `USES_THIRD_PARTY_CONTENT`. Confirmed
        live: a hard requirement for review submission that nothing else in this tool surface checks
        or writes — asogenic_get_release_readiness blocks on this being unset.

        Returns `{"set": True, "declaration": str}`. Raises a validation error for any value other
        than the two listed above, before ever calling ASC."""
        return release_setup.set_content_rights_declaration(app_id=app_id, declaration=declaration)


    @server.tool(name="asogenic_get_app_accessibility", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_app_accessibility")
    def asogenic_get_app_accessibility(app_id: str) -> dict[str, Any]:
        """Read the optional app-wide Accessibility URL and accessibility declarations by device family."""
        return release_setup.get_app_accessibility(app_id=app_id)


    @server.tool(name="asogenic_set_accessibility_url", annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    @log_tool_call("asogenic_set_accessibility_url")
    def asogenic_set_accessibility_url(app_id: str, accessibility_url: str) -> dict[str, Any]:
        """Set the optional app-wide public URL describing this app's accessibility support."""
        return release_setup.set_accessibility_url(app_id=app_id, accessibility_url=accessibility_url)


    @server.tool(name="asogenic_set_accessibility_declaration", annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
    @log_tool_call("asogenic_set_accessibility_declaration")
    def asogenic_set_accessibility_declaration(
        app_id: str, device_family: str, attributes: dict[str, bool], publish: bool = False
    ) -> dict[str, Any]:
        """Create or update one device family's Accessibility Nutrition Label. Pass only Apple's
        supported `supports*` boolean attributes; set `publish=True` when the declaration is ready."""
        return release_setup.set_accessibility_declaration(
            app_id=app_id, device_family=device_family, attributes=attributes, publish=publish
        )


    @server.tool(name="asogenic_get_age_rating", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_age_rating")
    def asogenic_get_age_rating(app_id: str, platform: str) -> dict[str, Any]:
        """The current age rating declaration's raw attributes, Apple's own field names. Call this
        before asogenic_set_age_rating to see the current shape and which fields exist — Apple has
        added fields to this resource over time and there's no schema-discovery tool, so the current
        live values are the best guide to what's expected (types especially: most fields are string
        enums like `"NONE"`/`"INFREQUENT_OR_MILD"`/`"FREQUENT_OR_INTENSE"`, but a few like
        `ageAssurance` are boolean — confirmed live this distinction isn't guessable and Apple
        returns a plain type-mismatch error, not a helpful one, if you get it wrong).

        Returns `{"attributes": dict | None}` — `attributes` is null if the declaration isn't
        reachable at all (rare; usually means the appInfo itself is in a bad state)."""
        return release_setup.get_age_rating(app_id=app_id, platform=platform)


    @server.tool(
        name="asogenic_set_age_rating",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_age_rating")
    def asogenic_set_age_rating(app_id: str, platform: str, attributes: dict[str, Any]) -> dict[str, Any]:
        """Update the age rating declaration. `attributes` uses Apple's own field names verbatim
        (call asogenic_get_age_rating first to see the current shape and field types) — the accuracy
        of what's declared is the caller's responsibility, not this tool's. ASC merges a partial
        attribute set onto the existing declaration, so you only need to send what's changing.
        Required before submission — asogenic_get_release_readiness blocks if the declaration isn't
        reachable at all, though it can't verify the *content* is accurate, only that one exists.

        Returns `{"set": True}`. Raises a validation error if `attributes` is empty, and surfaces
        Apple's own per-field type/enum errors verbatim on a bad value (e.g. a string where a
        boolean was expected) — there's no local validation of individual field values."""
        return release_setup.set_age_rating(app_id=app_id, platform=platform, attributes=attributes)


    @server.tool(name="asogenic_get_review_info", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_review_info")
    def asogenic_get_review_info(app_id: str, platform: str) -> dict[str, Any]:
        """The current App Review contact/demo-account/notes for this version.

        Returns `{contact_first_name, contact_last_name, contact_phone, contact_email,
        demo_account_name, demo_account_password, demo_account_required, notes}` — every field comes
        back as an empty string (`demo_account_required` as `False`) rather than null/missing when
        nothing has been set yet or there's no version at all, so a caller doesn't need to
        null-check each field individually."""
        return release_setup.get_review_info(app_id=app_id, platform=platform)


    @server.tool(
        name="asogenic_set_review_info",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_review_info")
    def asogenic_set_review_info(
        app_id: str,
        platform: str,
        contact_first_name: str = "",
        contact_last_name: str = "",
        contact_phone: str = "",
        contact_email: str = "",
        demo_account_name: str = "",
        demo_account_password: str = "",
        demo_account_required: bool | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """What the App Review team sees if they need to reach you or log in. Required before
        submission — asogenic_get_release_readiness blocks if `contact_email` is unset, and separately
        if `demo_account_required=True` but no demo_account_name/password is given.

        `contact_phone` must be `+<country code> <number, space-separated>` (e.g. `"+1 415 555
        0132"`) — confirmed live that Apple validates this strictly and rejects other formats (a
        bare `"+15555550100"` with no spaces failed) with a generic-sounding format error.

        Returns `{"set": True}`. Raises NOT_FOUND if there's no app store version yet to attach
        review info to."""
        return release_setup.set_review_info(
            app_id=app_id, platform=platform, contact_first_name=contact_first_name,
            contact_last_name=contact_last_name, contact_phone=contact_phone, contact_email=contact_email,
            demo_account_name=demo_account_name, demo_account_password=demo_account_password,
            demo_account_required=demo_account_required, notes=notes,
        )


    @server.tool(name="asogenic_list_builds", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_builds", quota_exempt=True)
    def asogenic_list_builds(app_id: str, platform: str | None = None) -> dict[str, Any]:
        """Every processed build for this app — whether one exists at all is invisible to every
        other tool here, since builds are uploaded via Xcode/Transporter/CI, entirely outside this
        server's reach. Call this to find a `build_id` for asogenic_attach_build.

        Returns `{"builds": [{id, version, processing_state, uses_non_exempt_encryption, expired},
        ...]}`. An empty list doesn't necessarily mean no build was ever uploaded — it can also mean
        Apple hasn't finished processing one yet; `processing_state` on an individual build tells you
        that. `uses_non_exempt_encryption` is null until asogenic_set_export_compliance has been
        called for that build — asogenic_get_release_readiness blocks on that being unset for
        whichever build is attached."""
        return release_setup.list_builds(app_id=app_id, platform=platform)


    @server.tool(
        name="asogenic_attach_build",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_attach_build")
    def asogenic_attach_build(app_id: str, platform: str, build_id: str) -> dict[str, Any]:
        """Attach an already-uploaded build (see asogenic_list_builds) to this app version. Required
        before submission — asogenic_get_release_readiness blocks if no build is attached. Call
        asogenic_set_export_compliance next for this same build_id.

        Returns `{"attached": True, "build_id": str}`. Raises NOT_FOUND if there's no app store
        version yet to attach a build to."""
        return release_setup.attach_build(app_id=app_id, platform=platform, build_id=build_id)


    @server.tool(
        name="asogenic_set_export_compliance",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_export_compliance")
    def asogenic_set_export_compliance(build_id: str, uses_non_exempt_encryption: bool) -> dict[str, Any]:
        """The common-case export compliance declaration for one build (no custom cryptography, or
        only exempt uses like standard HTTPS). An app with real custom crypto needs a fuller
        declaration this tool doesn't cover. Required (for the attached build) before submission —
        asogenic_get_release_readiness blocks on this being unset. The tool reads the existing build
        declaration first and skips an identical value, making retries and reconciliation safe.

        Returns `{"set": True, "uses_non_exempt_encryption": bool}`."""
        return release_setup.set_export_compliance(build_id=build_id, uses_non_exempt_encryption=uses_non_exempt_encryption)


    @server.tool(name="asogenic_list_price_points", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_price_points", quota_exempt=True)
    def asogenic_list_price_points(
        app_id: str, territory: str = "USA", customer_price: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Available Apple price-point ids for one territory — Apple prices by point id, not raw
        currency amount. Call this to find a `price_point_id` for asogenic_set_base_price (even a
        free app needs one — pass `customer_price="0.0"` to find it directly).

        Apple returns roughly 800-900 price points per territory — confirmed live that an unfiltered
        call overflows this server's response size limit. Pass `customer_price` (e.g. `"0.0"`) to
        look up one exact point by price; otherwise only the first `limit` points are returned (in
        Apple's own ascending-price order).

        Returns `{"price_points": [{id, customer_price, territory}, ...], "total_count": int,
        "truncated": bool}`. `truncated` is true when more points exist beyond `limit` and no
        `customer_price` filter was given."""
        return release_setup.list_price_points(app_id=app_id, territory=territory, customer_price=customer_price, limit=limit)


    @server.tool(
        name="asogenic_set_base_price",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_set_base_price")
    def asogenic_set_base_price(app_id: str, price_point_id: str, territory: str = "USA") -> dict[str, Any]:
        """Set this app's base price to one of the ids from asogenic_list_price_points. `territory`
        must match the territory that `price_point_id` was listed under (defaults to `"USA"` on both
        tools, so they line up if you don't override either). Every app needs a price schedule set
        before submission, including free apps — pick the `"0.0"` customer_price price point.

        Returns `{"set": True, "price_point_id": str, "territory": str}`."""
        return release_setup.set_base_price(app_id=app_id, price_point_id=price_point_id, territory=territory)


    @server.tool(name="asogenic_get_app_privacy", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_app_privacy")
    def asogenic_get_app_privacy(app_id: str) -> dict[str, Any]:
        """This app's current App Privacy ("nutrition label") declarations.

        NOT USABLE TODAY: confirmed live against a real ASC account, and confirmed via Fastlane's own
        documentation and Apple developer forum threads, that Apple has never exposed App Privacy
        management to API-key authentication at all — every path this tool tries (and every
        plausible variant tried live: nested under `apps`, under `appInfos`, top-level, `/relationships/`)
        returns a PATH_ERROR/NOT_FOUND, not real data. This is Apple review's #1 rejection reason and
        currently has exactly one working path: App Store Connect's own web UI (App Privacy tab).
        This tool is kept because the resource names are real (Apple's own submission-time error
        payloads reference them) and Apple could expose a working route in the future — but don't
        plan a workflow around it succeeding right now.

        Always raises a typed `APP_PRIVACY_NO_API` error (not Apple's raw PATH_ERROR) telling the
        caller to use App Store Connect's web UI — see docs/interfaces/errors.md."""
        return release_setup.get_app_privacy(app_id=app_id)


    @server.tool(
        name="asogenic_add_app_privacy_declaration",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_add_app_privacy_declaration")
    def asogenic_add_app_privacy_declaration(
        app_id: str, category_id: str, data_protection_id: str, purpose_ids: list[str]
    ) -> dict[str, Any]:
        """Add one data-type declaration to the App Privacy label. `category_id`/`data_protection_id`/
        `purpose_ids` are Apple's own fixed reference ids from the privacy questionnaire — this
        writes exactly what it's given; getting the declaration right is the caller's job.

        NOT USABLE TODAY — see asogenic_get_app_privacy's docstring: there is no working API-key path
        for App Privacy at all (confirmed live and via Fastlane's documentation). Use App Store
        Connect's web UI (App Privacy tab) instead. Always raises a typed `APP_PRIVACY_NO_API`
        error, not Apple's raw PATH_ERROR."""
        return release_setup.add_app_privacy_declaration(
            app_id=app_id, category_id=category_id, data_protection_id=data_protection_id, purpose_ids=purpose_ids
        )


    @server.tool(
        name="asogenic_remove_app_privacy_declaration",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_remove_app_privacy_declaration")
    def asogenic_remove_app_privacy_declaration(usage_id: str) -> dict[str, Any]:
        """Remove one App Privacy declaration by its id (from asogenic_get_app_privacy).

        NOT USABLE TODAY — see asogenic_get_app_privacy's docstring: no working API-key path exists
        for App Privacy. Use App Store Connect's web UI instead. Always raises a typed
        `APP_PRIVACY_NO_API` error."""
        return release_setup.remove_app_privacy_declaration(usage_id=usage_id)


    @server.tool(
        name="asogenic_set_app_privacy_publish_state",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_app_privacy_publish_state")
    def asogenic_set_app_privacy_publish_state(app_id: str, published: bool) -> dict[str, Any]:
        """Make the current App Privacy declarations visible on the listing (or unpublish them) —
        declaring individual entries alone doesn't do this.

        NOT USABLE TODAY — see asogenic_get_app_privacy's docstring: confirmed live that
        `appDataUsagePublishState` isn't even a valid field name via the API, let alone writable. Use
        App Store Connect's web UI instead (fill in the questionnaire there and its own Publish
        action handles this). Always raises a typed `APP_PRIVACY_NO_API` error."""
        return release_setup.set_app_privacy_publish_state(app_id=app_id, published=published)
