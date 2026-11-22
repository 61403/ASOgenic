"""The pre-flight checklist and the two tools around it: recording what an app actually *is*
(asogenic_intake_product_context — nothing else here can generate real metadata without this),
and the readiness check that ties every other tool's coverage together into one "here's what's
still blocking submission" answer instead of an agent discovering each gap one ASC error at a
time. Also asogenic_submit_for_review — the actual trigger, which no existing tool provides:
asogenic_publish only ever PATCHes metadata fields, it never asks Apple to review the version.
"""

from __future__ import annotations

from typing import Any

from asogenic import context
from asogenic.errors import ASOgenicError, asc_state_error, validation_error
from asogenic.session import session_key as make_session_key


def intake_product_context(
    *,
    app_id: str,
    platform: str,
    what_it_does: str,
    target_audience: str = "",
    key_features: list[str] | None = None,
    requires_account: bool = False,
    demo_account_notes: str = "",
    privacy_policy_url: str = "",
    support_url: str = "",
    collects_user_data: bool | None = None,
) -> dict[str, Any]:
    """Record what this app actually is, once, so every generation tool downstream (keyword
    research, asogenic_assemble_locale, asogenic_optimize_source_search) has real grounding
    instead of guessing from bare seed words — this is the gap a real run against a brand-new
    app surfaced: `asogenic_research_keywords` with generic guessed seeds confidently inferred
    the wrong category, because nothing upstream of it knew what the app was for.

    `collects_user_data` isn't itself a privacy declaration (see asogenic_add_app_privacy_declaration
    for that) — it's just enough for asogenic_get_release_readiness to flag "you said this app
    collects data but no privacy declaration exists yet" as a real gap, not a guess."""
    if not what_it_does.strip():
        raise validation_error("what_it_does must not be empty", field_name="what_it_does")

    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    key = make_session_key(bundle_id=app_id, platform=platform, version_id=resolved.version_id)
    session = context.SESSION_STORE.get_or_create(key)
    session.product_context = {
        "what_it_does": what_it_does.strip(),
        "target_audience": target_audience.strip(),
        "key_features": list(key_features or []),
        "requires_account": requires_account,
        "demo_account_notes": demo_account_notes.strip(),
        "privacy_policy_url": privacy_policy_url.strip(),
        "support_url": support_url.strip(),
        "collects_user_data": collects_user_data,
    }
    context.SESSION_STORE.flush(key)
    return {"session_key": key, "stored": True}


def _check(blockers: list[str], warnings: list[str], *, condition: bool, message: str, is_warning: bool = False) -> None:
    if condition:
        return
    (warnings if is_warning else blockers).append(message)


def get_release_readiness(
    *, app_id: str, platform: str, required_display_types: list[str] | None = None
) -> dict[str, Any]:
    """Everything currently blocking (or worth checking before) submitting this app+platform for
    Apple review, gathered from real ASC state in one call instead of an agent piecing it
    together from a dozen separate reads and discovering the rest as publish-time errors.
    `blockers` are things Apple's own review flow requires; `warnings` are real gaps this
    server's own tools can't fill for you (e.g. no product context recorded) that won't stop a
    submission but will produce worse generated content."""
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)

    blockers: list[str] = []
    warnings: list[str] = []

    if resolved.version_id is None:
        blockers.append("no app store version exists for this platform — create one in App Store Connect first")
        return {"ready": False, "blockers": blockers, "warnings": warnings}

    locales = client.fetch_all_localizations(resolved.app_info_id, resolved.version_id)
    primary = next(iter(locales.values()), None)
    _check(blockers, warnings, condition=primary is not None and bool(primary.name), message="app name is empty")
    _check(blockers, warnings, condition=primary is not None and bool(primary.description), message="description is empty")
    _check(
        blockers, warnings,
        condition=primary is not None and bool(primary.privacy_policy_url),
        message="privacy_policy_url is not set (required for every submission)",
    )
    _check(
        blockers, warnings,
        condition=primary is not None and bool(primary.support_url),
        message="support_url is not set (required by App Review)",
    )

    # "What's New" (whatsNew) must be present on EVERY version localization once the app has a
    # prior version — Apple rejects the reviewSubmissionItem create otherwise, naming each
    # offending appStoreVersionLocalization, and nothing else here catches it (asogenic_publish
    # skips empty fields silently, so a new version that inherited description/keywords for a
    # secondary locale but not whats_new sails through publish and only fails at submit).
    # Confirmed live: a 1.8 submission 409'd on exactly this for 4 inherited locales.
    version_locales_missing_whats_new = sorted(
        loc for loc, data in locales.items()
        if data.version_localization_id is not None
        and (data.description or data.keywords or data.promotional_text)
        and not data.whats_new
    )
    if version_locales_missing_whats_new:
        try:
            is_update = client.count_app_store_versions(resolved.app_id, platform=platform) > 1
        except ASOgenicError:
            # If a locale already has whats_new, the rest must match it regardless of version count.
            is_update = any(
                data.whats_new for data in locales.values() if data.version_localization_id is not None
            )
        if is_update:
            blockers.append(
                "\"What's New\" (whats_new) is required on every localization of an app update but is "
                f"empty for: {', '.join(version_locales_missing_whats_new)}. Set it per locale "
                "(asogenic_fetch_source → asogenic_approve_locale → asogenic_publish), or remove "
                "those localizations if they were only inherited and aren't wanted."
            )

    primary_cat, _secondary_cat = client.get_category(resolved.app_info_id)
    _check(blockers, warnings, condition=primary_cat is not None, message="no primary category set (asogenic_set_category)")

    age_rating = client.get_age_rating_declaration(resolved.app_info_id)
    # A declaration resource is created automatically, so its existence (and even one answered
    # field) does not mean the questionnaire is complete.  Apple's response only includes the
    # questions applicable to the account/API version; every returned question except optional
    # age-rating overrides must be answered.
    optional_age_rating_fields = {
        "ageRatingOverride", "ageRatingOverrideV2", "koreaAgeRatingOverride",
        "gracRatingClassificationNumber", "kidsAgeBand", "developerAgeRatingInfoUrl",
    }
    unanswered_age_rating_fields = (
        sorted(
            field for field, value in age_rating.items()
            if field not in optional_age_rating_fields and value in (None, "")
        )
        if isinstance(age_rating, dict) else []
    )
    age_rating_complete = isinstance(age_rating, dict) and bool(age_rating) and not unanswered_age_rating_fields
    _check(
        blockers, warnings, condition=age_rating_complete,
        message=(
            "age rating declaration is missing or has unanswered required question(s)"
            + (f": {', '.join(unanswered_age_rating_fields)}" if unanswered_age_rating_fields else "")
            + " — complete it with asogenic_set_age_rating"
        ),
    )

    try:
        _check(
            blockers, warnings,
            condition=client.get_app_price_schedule(resolved.app_id),
            message="no base price schedule is set, including a free (0.0) price (asogenic_set_base_price)",
        )
    except ASOgenicError as exc:
        warnings.append(f"could not check app price schedule: {exc}")

    try:
        availability = client.get_app_availability(resolved.app_id)
        has_available_territory = any(
            bool((item.get("attributes") or {}).get("available")) for item in availability
        )
        _check(
            blockers, warnings,
            condition=has_available_territory,
            message=(
                "no App Store territory is available — configure App Availability in App Store Connect "
                "before submitting (Apple exposes this API for verification, while normal release "
                "territory configuration remains a web-UI task)"
            ),
        )
    except ASOgenicError as exc:
        warnings.append(f"could not check app availability: {exc}")

    # Both confirmed live against a real reviewSubmissionItems create call — Apple rejects the
    # submission outright without them, but neither is checked anywhere else in this app's
    # lifecycle (they don't block asogenic_publish, and the old appStoreVersionSubmissions flow
    # never surfaced them either since it was defunct for CREATE regardless).
    try:
        version_copyright = client.get_version_copyright(resolved.version_id)
        _check(blockers, warnings, condition=bool(version_copyright), message="version copyright is not set (asogenic_set_version_copyright)")
    except ASOgenicError as exc:
        warnings.append(f"could not check version copyright: {exc}")

    try:
        content_rights = client.get_content_rights_declaration(resolved.app_id)
        _check(blockers, warnings, condition=bool(content_rights), message="contentRightsDeclaration is not set (asogenic_set_content_rights_declaration)")
    except ASOgenicError as exc:
        warnings.append(f"could not check contentRightsDeclaration: {exc}")

    # Each of the following checks hits a distinct ASC sub-resource; any one of them failing
    # (a transient ASC error, or — as app privacy turned out to be for this account — a
    # resource that isn't reachable via this API at all) must not take the whole readiness
    # report down with it. That was the actual root cause of asogenic_submit_for_review being
    # completely unusable: one broken sub-check raised, so the caller never even got to see the
    # rest of the picture. Every check below degrades to a warning on unexpected failure instead.
    builds: list[Any] = []
    try:
        builds = client.list_builds(resolved.app_id, platform=platform)
        usable_builds = [b for b in builds if not b.expired]
        _check(blockers, warnings, condition=bool(usable_builds), message="no non-expired build has been uploaded (via Xcode/Transporter/CI — outside this server's reach)")
    except ASOgenicError as exc:
        warnings.append(f"could not check builds: {exc}")

    try:
        attached_build_id = client.get_attached_build(resolved.version_id)
        _check(blockers, warnings, condition=attached_build_id is not None, message="no build attached to this version (asogenic_attach_build)")
        if attached_build_id is not None:
            attached = next((b for b in builds if b.id == attached_build_id), None)
            if attached is not None:
                _check(
                    blockers, warnings, condition=attached.uses_non_exempt_encryption is not None,
                    message="the attached build has no export compliance declaration (asogenic_set_export_compliance)",
                )
    except ASOgenicError as exc:
        warnings.append(f"could not check the attached build: {exc}")

    try:
        review_detail = client.get_app_review_detail(resolved.version_id)
        has_contact = bool(review_detail and review_detail.contact_email)
        _check(blockers, warnings, condition=has_contact, message="no review contact email set (asogenic_set_review_info)")
        if review_detail and review_detail.demo_account_required:
            _check(
                blockers, warnings,
                condition=bool(review_detail.demo_account_name and review_detail.demo_account_password),
                message="demo account marked required but no demo account name/password is set",
            )
    except ASOgenicError as exc:
        warnings.append(f"could not check review info: {exc}")

    try:
        screenshot_locales_checked = 0
        has_any_screenshot = False
        failed_screenshot_ids: list[str] = []
        all_screenshot_ids: list[str] = []
        for ids in client.list_locales(resolved.app_info_id, resolved.version_id).values():
            if ids.version_localization_id is None:
                continue
            screenshot_locales_checked += 1
            sets = client.list_screenshot_sets(ids.version_localization_id)
            for s in sets:
                if s.screenshot_ids:
                    has_any_screenshot = True
                    all_screenshot_ids.extend(s.screenshot_ids)
            if screenshot_locales_checked >= 3:  # a full per-locale scan isn't needed to answer "any at all"
                break
        _check(blockers, warnings, condition=has_any_screenshot, message="no screenshots uploaded for any locale (asogenic_upload_screenshot)")

        if required_display_types:
            present = {
                s.display_type
                for ids in client.list_locales(resolved.app_info_id, resolved.version_id).values()
                if ids.version_localization_id is not None
                for s in client.list_screenshot_sets(ids.version_localization_id)
                if s.screenshot_ids
            }
            missing = [value for value in required_display_types if value not in present]
            if missing:
                blockers.append(
                    "required screenshot display type(s) are missing: "
                    + ", ".join(missing)
                    + " (asogenic_upload_screenshot)"
                )

        # Presence alone isn't enough — confirmed live that a screenshot can commit successfully
        # through asogenic_upload_screenshot and still be permanently rejected by Apple's asset
        # pipeline afterward (e.g. IMAGE_ALPHA_NOT_ALLOWED), invisible until a real submission
        # attempt discovers it. Capped the same way the locale scan above is, to bound the cost.
        for screenshot_id in all_screenshot_ids[:5]:
            state = client.get_screenshot_delivery_state(screenshot_id)
            if state.get("state") == "FAILED":
                failed_screenshot_ids.append(screenshot_id)
        if failed_screenshot_ids:
            blockers.append(
                f"{len(failed_screenshot_ids)} uploaded screenshot(s) were rejected by Apple's "
                f"asset pipeline and must be replaced (asogenic_get_screenshot_status): "
                f"{', '.join(failed_screenshot_ids)}"
            )

        if required_display_types:
            warnings.append(
                "the explicitly requested screenshot display types were checked; Apple may still "
                "scale a highest-resolution asset to optional smaller sizes, and submission-time "
                "validation remains authoritative for any unsupported build/device combination"
            )
        else:
            warnings.append(
                "screenshot coverage is only partially verified — Apple requires at least an "
                "iPhone Dynamic Island medium screenshot, plus an iPad 13-inch screenshot when "
                "the app supports iPadOS; pass the current display identifiers in "
                "required_display_types to make those device family checks explicit, because build device "
                "coverage is not reliably derivable from ASC metadata alone"
            )
    except ASOgenicError as exc:
        warnings.append(f"could not check screenshots: {exc}")

    key = make_session_key(bundle_id=app_id, platform=platform, version_id=resolved.version_id)
    session = context.SESSION_STORE.get(key)
    product_context = session.product_context if session else None
    if product_context is None:
        warnings.append("no product context recorded (asogenic_intake_product_context) — generated copy is likely guessing")

    try:
        privacy_declarations = client.list_app_data_usages(resolved.app_id)
        if product_context and product_context.get("collects_user_data") and not privacy_declarations:
            blockers.append("product context says this app collects user data, but no App Privacy declaration exists (asogenic_add_app_privacy_declaration)")
        if not privacy_declarations and (product_context is None or product_context.get("collects_user_data") is None):
            warnings.append("App Privacy has no declarations and no product context confirms whether that's correct (\"collects no data\" must be an explicit answer, not an absence of one)")
    except ASOgenicError as exc:
        # Confirmed live against a real ASC account: appDataUsages is not reachable as a
        # relationship on either `apps` or `appInfos`, and no top-level collection route for it
        # exists either — this isn't a transient error, App Privacy is not manageable through
        # this API surface for this account today. Apple's own App Store Connect web UI (App
        # Privacy tab) is the only way to fill this in; still a real blocker, just not one this
        # server can read or write, so it can't be a hard blocker here without also being an
        # unfixable dead end for the caller.
        warnings.append(
            f"App Privacy can't be read via the API ({exc}) — Apple has never exposed the "
            "nutrition label to key-based auth, so this server can't confirm its state either "
            "way. This is NOT a per-version task: App Privacy is set once at the app level and "
            "carries forward to every later version. If it was already completed for a prior "
            "release (any app that has passed review has one), there is nothing to do here. "
            "Only revisit it if THIS release changed what data the app collects or how it's "
            "used — then update it in App Store Connect's App Privacy tab (web UI only) and "
            "press Publish there before submitting. Left unset entirely, it is Apple review's "
            "most common rejection reason."
        )

    return {"ready": not blockers, "blockers": blockers, "warnings": warnings}


def submit_for_review(*, app_id: str, platform: str, force: bool = False) -> dict[str, Any]:
    """Fires the actual "submit this version for Apple review" action — distinct from
    asogenic_publish, which only ever writes metadata fields. Runs asogenic_get_release_readiness
    first and refuses if anything is blocking, unless `force=True` (Apple will reject it anyway
    in that case; force exists for a caller that wants Apple's own error text instead of this
    server's readiness summary).

    Drives Apple's current review-submission flow (reviewSubmissions + reviewSubmissionItems),
    not the legacy appStoreVersionSubmissions resource this used to target — confirmed live that
    the legacy resource is defunct for CREATE (`FORBIDDEN_ERROR: ... Allowed operation is:
    DELETE`) regardless of how ready the version is; every real app must go through
    reviewSubmissions today. If an active (non-terminal) reviewSubmission already exists for this
    app+platform, its item is attached/confirmed rather than creating a duplicate — Apple only
    allows one in flight per app+platform at a time."""
    if not force:
        readiness = get_release_readiness(app_id=app_id, platform=platform)
        if not readiness["ready"]:
            raise asc_state_error(
                "not ready for submission: " + "; ".join(readiness["blockers"]),
                code="STATE_ERROR",
                detail={"blockers": readiness["blockers"], "warnings": readiness["warnings"]},
            )

    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version to submit", code="NOT_FOUND")

    review_submission_id = client.get_active_review_submission(resolved.app_id, platform=platform)
    if review_submission_id is None:
        review_submission_id = client.create_review_submission(resolved.app_id, platform=platform)
    # A resumed submission (e.g. a prior attempt that failed readiness checks Apple enforces but
    # this server didn't, like copyright) can already exist with no item on it yet — confirmed
    # live: skipping this for an already-existing submission left it permanently itemless, and
    # confirm_review_submission failed forever after with "must have an appStoreVersions
    # included in this review submission", regardless of how many times readiness was fixed and
    # rechecked.
    if not client.list_review_submission_items(review_submission_id):
        client.add_review_submission_item(review_submission_id, version_id=resolved.version_id)
    client.confirm_review_submission(review_submission_id)
    state = client.get_review_submission_state(review_submission_id)
    # READY_FOR_REVIEW is the draft state. A successful PATCH alone is not proof that ASC
    # accepted the version into its review queue, which previously made stale drafts look sent.
    submitted = state not in (None, "READY_FOR_REVIEW", "CANCELED")
    return {
        "submitted": submitted,
        "review_submission_id": review_submission_id,
        "state": state,
        "next_action": (
            "Review submission is awaiting Apple review."
            if submitted else
            "Apple kept this as a draft review submission; inspect the state and item validation before retrying."
        ),
    }


def cancel_review_submission(*, app_id: str, platform: str) -> dict[str, Any]:
    """Withdraws this app+platform's active review submission (asogenic_submit_for_review's
    counterpart — "I want this back out of Apple's queue"). Only works while Apple hasn't
    finished reviewing it yet: confirmed live that a submission which already reached `COMPLETE`
    (Apple has approved, rejected, or otherwise finished with it) is not cancellable — that state
    can arrive surprisingly fast, sometimes under a minute after asogenic_submit_for_review
    returns, so don't assume there's a wide window to act in. If there's nothing active to
    cancel, this raises NOT_FOUND rather than silently succeeding, so a caller can't mistake "no
    submission existed" for "successfully canceled one"."""
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    review_submission_id = client.get_active_review_submission(resolved.app_id, platform=platform)
    if review_submission_id is None:
        raise asc_state_error(
            f"no active review submission for app '{app_id}' / platform '{platform}' to cancel",
            code="NOT_FOUND",
        )
    client.cancel_review_submission(review_submission_id)
    return {"canceled": True, "review_submission_id": review_submission_id}


def get_review_submission_status(*, app_id: str, platform: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    submission_id = client.get_active_review_submission(resolved.app_id, platform=platform)
    if submission_id is None:
        return {"active": False, "review_submission_id": None, "state": None, "item_ids": []}
    return {
        "active": True,
        "review_submission_id": submission_id,
        "state": client.get_review_submission_state(submission_id),
        "item_ids": client.list_review_submission_items(submission_id),
    }
