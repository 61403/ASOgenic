"""Everything a first-time submission needs beyond text metadata: category, age rating, review
info, export compliance, build selection, and pricing. Added after driving the existing tool
surface against a real, never-launched app end to end and finding that every existing tool
assumes the app already has a live listing to read from or revise — these don't; they're the
tools that get a *new* app to the point where it has one. Same house rules as publish.py: no AI,
no judgment, mechanical reads/writes onto ASC. The accuracy of what gets declared (an age rating,
a privacy category) is the caller's responsibility, not this server's.
"""

from __future__ import annotations

from typing import Any

from asogenic import context
from asogenic.errors import asc_state_error, validation_error


def list_categories() -> dict[str, Any]:
    """Apple's fixed App Store category list — what `set_category` picks IDs from. Unlike
    asogenic_resolve_genre (which only works for an app with an existing public listing), this
    works before an app has ever shipped."""
    client = context.get_asc_client()
    return {"categories": client.list_categories()}


VALID_RELEASE_TYPES = frozenset({"MANUAL", "AFTER_APPROVAL", "SCHEDULED"})
ACCESSIBILITY_DEVICE_FAMILIES = frozenset({"IPHONE", "IPAD", "APPLE_TV", "APPLE_WATCH", "MAC", "VISION"})
ACCESSIBILITY_FEATURES = frozenset({
    "supportsAudioDescriptions", "supportsCaptions", "supportsDarkInterface",
    "supportsDifferentiateWithoutColorAlone", "supportsLargerText", "supportsReducedMotion",
    "supportsSufficientContrast", "supportsVoiceControl", "supportsVoiceover",
})


def create_version(
    *, app_id: str, platform: str, version_string: str, release_type: str | None = None
) -> dict[str, Any]:
    """Create a new appStoreVersions record — the only way to get a fresh editable version once
    the current one has already shipped (every other tool here reads or revises a version that
    already exists; none of them can create the next one). Fails loudly if an editable version
    already exists for this platform, since Apple only allows one at a time."""
    if not version_string.strip():
        raise validation_error("version_string must not be empty", field_name="version_string")
    if release_type is not None and release_type not in VALID_RELEASE_TYPES:
        raise validation_error(
            f"release_type must be one of {sorted(VALID_RELEASE_TYPES)}, got '{release_type}'",
            field_name="release_type",
        )
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    _, is_editable, version_state = client.resolve_app_store_version(numeric_app_id, platform=platform)
    if is_editable:
        raise asc_state_error(
            f"app '{app_id}' already has an editable app store version for platform '{platform}' "
            f"(state={version_state}) — Apple allows only one editable version at a time",
            code="STATE_ERROR",
        )
    version_id = client.create_app_store_version(
        numeric_app_id, platform=platform, version_string=version_string.strip(), release_type=release_type
    )
    return {"created": True, "version_id": version_id, "version_string": version_string.strip()}


def set_version_copyright(*, app_id: str, platform: str, copyright_text: str) -> dict[str, Any]:
    """`copyright` on appStoreVersions — confirmed live to be a hard requirement for review
    submission ("You must provide a value for the attribute 'copyright'"), invisible until the
    actual reviewSubmissionItems create call, since neither the old readiness check nor any
    other tool here ever read or wrote it."""
    if not copyright_text.strip():
        raise validation_error("copyright_text must not be empty", field_name="copyright_text")
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version to set copyright on", code="NOT_FOUND")
    client.set_version_copyright(resolved.version_id, copyright_text=copyright_text.strip())
    return {"set": True, "copyright": copyright_text.strip()}


def set_content_rights_declaration(*, app_id: str, declaration: str) -> dict[str, Any]:
    """`contentRightsDeclaration` on `apps` — confirmed live to be a hard requirement for review
    submission. `declaration` must be one of Apple's own enum values:
    `DOES_NOT_USE_THIRD_PARTY_CONTENT` or `USES_THIRD_PARTY_CONTENT`."""
    valid = {"DOES_NOT_USE_THIRD_PARTY_CONTENT", "USES_THIRD_PARTY_CONTENT"}
    if declaration not in valid:
        raise validation_error(
            f"declaration must be one of {sorted(valid)}, got '{declaration}'", field_name="declaration"
        )
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    client.set_content_rights_declaration(numeric_app_id, declaration=declaration)
    return {"set": True, "declaration": declaration}


def get_app_accessibility(*, app_id: str) -> dict[str, Any]:
    """Return the app-wide accessibility URL and one declaration per device family."""
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    app = client.get_resource(f"/v1/apps/{numeric_app_id}", params={"fields[apps]": "accessibilityUrl"})
    data = app.get("data") if isinstance(app, dict) else {}
    attrs = data.get("attributes") if isinstance(data, dict) else {}
    declarations = client.list_resource(
        f"/v1/apps/{numeric_app_id}/accessibilityDeclarations",
        params={"fields[accessibilityDeclarations]": "deviceFamily,state," + ",".join(sorted(ACCESSIBILITY_FEATURES))},
    )
    return {"accessibility_url": (attrs or {}).get("accessibilityUrl"), "declarations": declarations}


def set_accessibility_url(*, app_id: str, accessibility_url: str) -> dict[str, Any]:
    if not accessibility_url.strip():
        raise validation_error("accessibility_url must not be empty", field_name="accessibility_url")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    client.update_resource("apps", numeric_app_id, attributes={"accessibilityUrl": accessibility_url.strip()})
    return {"set": True, "accessibility_url": accessibility_url.strip()}


def set_accessibility_declaration(
    *, app_id: str, device_family: str, attributes: dict[str, bool], publish: bool = False
) -> dict[str, Any]:
    """Create or update an accessibility declaration for one Apple device family."""
    if device_family not in ACCESSIBILITY_DEVICE_FAMILIES:
        raise validation_error(
            f"device_family must be one of {sorted(ACCESSIBILITY_DEVICE_FAMILIES)}",
            field_name="device_family",
        )
    invalid = set(attributes) - ACCESSIBILITY_FEATURES
    if invalid or not attributes or any(not isinstance(value, bool) for value in attributes.values()):
        raise validation_error(
            "attributes must contain one or more supported accessibility feature names with boolean values",
            field_name="attributes",
        )
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    declarations = client.list_resource(
        f"/v1/apps/{numeric_app_id}/accessibilityDeclarations",
        params={"filter[deviceFamily]": device_family},
    )
    existing = next(
        (item for item in declarations if (item.get("attributes") or {}).get("deviceFamily") == device_family),
        None,
    )
    payload = dict(attributes)
    if existing and isinstance(existing.get("id"), str):
        client.update_resource("accessibilityDeclarations", existing["id"], attributes=payload)
        if publish:
            # Publishing is a state transition, not a feature attribute.  Keep it
            # separate so ASC validates the feature edit while the declaration is
            # still a draft, then publish the resulting draft explicitly.
            client.update_resource("accessibilityDeclarations", existing["id"], attributes={"publish": True})
        return {"set": True, "created": False, "id": existing["id"], "device_family": device_family, "published": publish}
    created = client.create_resource(
        "accessibilityDeclarations",
        attributes={"deviceFamily": device_family, **payload},
        relationships={"app": {"data": {"type": "apps", "id": numeric_app_id}}},
    )
    created_data = created.get("data") if isinstance(created, dict) else {}
    declaration_id = (created_data or {}).get("id")
    if publish and isinstance(declaration_id, str):
        client.update_resource("accessibilityDeclarations", declaration_id, attributes={"publish": True})
    return {"set": True, "created": True, "id": declaration_id, "device_family": device_family, "published": publish}


def set_category(
    *, app_id: str, platform: str, primary_category_id: str, secondary_category_id: str | None = None,
    primary_subcategory_one_id: str | None = None, primary_subcategory_two_id: str | None = None,
    secondary_subcategory_one_id: str | None = None, secondary_subcategory_two_id: str | None = None,
) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    client.set_category(
        resolved.app_info_id,
        primary_category_id=primary_category_id,
        secondary_category_id=secondary_category_id,
        primary_subcategory_one_id=primary_subcategory_one_id,
        primary_subcategory_two_id=primary_subcategory_two_id,
        secondary_subcategory_one_id=secondary_subcategory_one_id,
        secondary_subcategory_two_id=secondary_subcategory_two_id,
    )
    result = {
        "set": True,
        "primary_category_id": primary_category_id,
        "secondary_category_id": secondary_category_id,
    }
    result.update({
        key: value for key, value in {
            "primary_subcategory_one_id": primary_subcategory_one_id,
            "primary_subcategory_two_id": primary_subcategory_two_id,
            "secondary_subcategory_one_id": secondary_subcategory_one_id,
            "secondary_subcategory_two_id": secondary_subcategory_two_id,
        }.items() if value is not None
    })
    return result


def get_age_rating(*, app_id: str, platform: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    attributes = client.get_age_rating_declaration(resolved.app_info_id)
    return {"attributes": attributes}


def set_age_rating(*, app_id: str, platform: str, attributes: dict[str, Any]) -> dict[str, Any]:
    """`attributes` uses Apple's own ageRatingDeclarations field names verbatim (e.g.
    `violenceCartoonOrFantasy: "NONE"|"INFREQUENT_OR_MILD"|"FREQUENT_OR_INTENSE"`,
    `gamblingAndContests: bool`) — call asogenic_get_age_rating first to see the current shape
    and fill in only what needs to change; ASC merges a partial attribute set onto the existing
    declaration."""
    # Apple returns nullable fields from get_age_rating. Passing that response back verbatim
    # makes Apple's PATCH reject otherwise valid updates with "value cannot be null". Treat
    # nulls as omitted fields; False, 0, and empty collections remain meaningful values.
    attributes = {key: value for key, value in attributes.items() if value is not None}
    if not attributes:
        raise validation_error("attributes must not be empty", field_name="attributes")
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    client.set_age_rating_declaration(resolved.app_info_id, attributes=attributes)
    return {"set": True}


def get_review_info(*, app_id: str, platform: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        return {"contact_first_name": "", "contact_last_name": "", "contact_phone": "", "contact_email": "", "demo_account_name": "", "demo_account_password": "", "demo_account_required": False, "notes": ""}
    detail = client.get_app_review_detail(resolved.version_id)
    if detail is None:
        return {"contact_first_name": "", "contact_last_name": "", "contact_phone": "", "contact_email": "", "demo_account_name": "", "demo_account_password": "", "demo_account_required": False, "notes": ""}
    return {
        "contact_first_name": detail.contact_first_name,
        "contact_last_name": detail.contact_last_name,
        "contact_phone": detail.contact_phone,
        "contact_email": detail.contact_email,
        "demo_account_name": detail.demo_account_name,
        "demo_account_password": detail.demo_account_password,
        "demo_account_required": detail.demo_account_required,
        "notes": detail.notes,
    }


def set_review_info(
    *,
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
    """What the App Review team sees if they need to reach you or log in — required whenever the
    app is gated behind an account. `demo_account_required=True` with no demo_account_name/
    password is a common real rejection reason; this tool doesn't block that combination itself
    (asogenic_get_release_readiness does), since a reviewer-facing note explaining "no login
    needed" is sometimes the correct answer instead."""
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version to attach review info to", code="NOT_FOUND")
    client.set_app_review_detail(
        resolved.version_id,
        contact_first_name=contact_first_name,
        contact_last_name=contact_last_name,
        contact_phone=contact_phone,
        contact_email=contact_email,
        demo_account_name=demo_account_name,
        demo_account_password=demo_account_password,
        demo_account_required=demo_account_required,
        notes=notes,
    )
    return {"set": True}


def list_builds(*, app_id: str, platform: str | None = None) -> dict[str, Any]:
    """Every processed build for this app (optionally filtered to one platform) — whether one
    exists at all is invisible to every other tool here, since builds are uploaded via Xcode/
    Transporter/CI, entirely outside this MCP server's reach."""
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    builds = client.list_builds(numeric_app_id, platform=platform)
    return {
        "builds": [
            {
                "id": b.id,
                "version": b.version,
                "processing_state": b.processing_state,
                "uses_non_exempt_encryption": b.uses_non_exempt_encryption,
                "expired": b.expired,
            }
            for b in builds
        ]
    }


def attach_build(*, app_id: str, platform: str, build_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version to attach a build to", code="NOT_FOUND")
    client.attach_build(resolved.version_id, build_id)
    return {"attached": True, "build_id": build_id}


def set_export_compliance(*, build_id: str, uses_non_exempt_encryption: bool) -> dict[str, Any]:
    """The common-case declaration only (no custom cryptography, or only exempt uses like
    standard HTTPS). An app with real custom crypto needs a fuller encryption declaration this
    tool doesn't cover — see the ASCClient.set_export_compliance docstring."""
    client = context.get_asc_client()
    client.set_export_compliance(build_id, uses_non_exempt_encryption=uses_non_exempt_encryption)
    return {"set": True, "uses_non_exempt_encryption": uses_non_exempt_encryption}


def list_price_points(
    *, app_id: str, territory: str = "USA", customer_price: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Apple returns roughly 800-900 price points per territory — enough to blow past this
    server's response size limit if returned unfiltered (confirmed live: an unfiltered call came
    back as an 85KB/801-item payload). Pass `customer_price` (e.g. "0.0" for the free tier) to
    look up one exact point; otherwise the first `limit` points are returned, in Apple's own
    ascending-price order, along with `total_count` and `truncated` so a caller knows more exist."""
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    points = client.list_price_points(numeric_app_id, territory=territory)
    if customer_price is not None:
        points = [p for p in points if p.customer_price == customer_price]
    total_count = len(points)
    truncated = customer_price is None and total_count > limit
    points = points[:limit] if customer_price is None else points
    return {
        "price_points": [{"id": p.id, "customer_price": p.customer_price, "territory": p.territory} for p in points],
        "total_count": total_count,
        "truncated": truncated,
    }


def set_base_price(*, app_id: str, price_point_id: str, territory: str = "USA") -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    client.set_base_price(numeric_app_id, price_point_id=price_point_id, territory=territory)
    return {"set": True, "price_point_id": price_point_id, "territory": territory}


def get_app_privacy(*, app_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    return {"declarations": client.list_app_data_usages(numeric_app_id)}


def add_app_privacy_declaration(
    *, app_id: str, category_id: str, data_protection_id: str, purpose_ids: list[str]
) -> dict[str, Any]:
    """One data-type declaration for the App Privacy "nutrition label" — `category_id`,
    `data_protection_id`, and each id in `purpose_ids` are Apple's own fixed reference ids
    (documented in App Store Connect's own privacy questionnaire, not listable via this API).
    This writes exactly what it's given; getting the declaration right is the caller's job, not
    this tool's — see asogenic_set_app_privacy_publish_state for making declarations visible once
    every entry is correct.

    NOTE (confirmed live against a real ASC account): `appDataUsages` is not currently reachable
    as a relationship on either `apps` or `appInfos`, and no top-level collection route for it
    responds either — every call this method makes fails with ASC's own PATH_ERROR regardless of
    payload. This is not a bug in the request shape this tool builds (which matches Apple's
    documented resource/relationship names); App Privacy appears not to be manageable through
    this API surface today. Until Apple exposes it, use App Store Connect's web UI (App Privacy
    tab) directly — asogenic_get_release_readiness reports this as a warning rather than
    silently succeeding or crashing the rest of the readiness check."""
    if not purpose_ids:
        raise validation_error("purpose_ids must not be empty", field_name="purpose_ids")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    usage_id = client.add_app_data_usage(
        numeric_app_id, category_id=category_id, data_protection_id=data_protection_id, purpose_ids=purpose_ids
    )
    return {"id": usage_id}


def remove_app_privacy_declaration(*, usage_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    client.delete_app_data_usage(usage_id)
    return {"removed": True}


def set_app_privacy_publish_state(*, app_id: str, published: bool) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    client.set_app_privacy_publish_state(numeric_app_id, published=published)
    return {"published": published}
