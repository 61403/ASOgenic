"""Screenshot management — the one required App Store asset no existing tool touches (every
other tool here is text). Apple's own upload flow is three steps (reserve, PUT the raw bytes to
a signed URL, commit with a checksum); this wraps all three behind one call so the client only
ever has to hand over the image bytes, not orchestrate Apple's asset-upload protocol itself.

Least live-tested part of this addition: the reserve/commit request shapes below follow Apple's
documented asset-upload pattern (the same shape appPreviews and build uploads use), but unlike
the rest of this server's ASC calls, no screenshot was actually uploaded against a real app while
building this — the metadata-only tools were verified live, this wasn't, since a real upload
writes an image to a real app's live listing.
"""

from __future__ import annotations

import time
from typing import Any

from asogenic import context
from asogenic.errors import ASOgenicError, asc_state_error, validation_error
from asogenic.tools.media_io import BinarySource, png_has_alpha, upload_operations

# How long/many times upload_screenshot polls assetDeliveryState after commit before giving up
# and reporting whatever it last saw. Apple's own processing is genuinely async and can outlast
# this window — that's fine, a caller unsure after this returns can keep polling themselves via
# asogenic_get_screenshot_status. What this window exists to catch is the FAILED case (e.g. an
# alpha channel Apple rejects outright), confirmed live to otherwise stay invisible until a
# submission attempt fails much later with the failure buried in unrelated ASC error detail.
_DELIVERY_POLL_ATTEMPTS = 5
_DELIVERY_POLL_INTERVAL_S = 2.0
MAX_SCREENSHOTS_PER_SET = 10
SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


def _require_screenshot_id(reservation: dict[str, Any]) -> str:
    screenshot_id = reservation.get("id")
    if not isinstance(screenshot_id, str) or not screenshot_id:
        raise asc_state_error(
            "Apple did not return an id while reserving the screenshot upload",
            code="MALFORMED_RESPONSE",
            detail=reservation,
        )
    return screenshot_id

def _resolve_version_localization_id(
    app_id: str, platform: str, locale: str, *, require_editable: bool = False
) -> str:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version", code="NOT_FOUND")
    if require_editable and not resolved.is_editable:
        raise asc_state_error(
            f"version for app '{app_id}' is not editable in its current state "
            f"({resolved.version_state or 'unknown'}); upload media on an editable version",
            code="VERSION_NOT_EDITABLE",
        )
    locale_ids = client.list_app_store_version_localizations(resolved.version_id)
    ids = locale_ids.get(locale)
    if ids is None or ids.version_localization_id is None:
        raise asc_state_error(
            f"locale '{locale}' has no app store version localization yet — create one first "
            "(asogenic_fetch_source/asogenic_publish for this locale) before adding screenshots",
            code="NOT_FOUND",
        )
    return ids.version_localization_id


def list_screenshots(*, app_id: str, platform: str, locale: str) -> dict[str, Any]:
    version_localization_id = _resolve_version_localization_id(app_id, platform, locale)
    client = context.get_asc_client()
    sets = client.list_screenshot_sets(version_localization_id)
    return {
        "screenshot_sets": [
            {"id": s.id, "display_type": s.display_type, "screenshot_ids": s.screenshot_ids} for s in sets
        ]
    }


def upload_screenshot(
    *,
    app_id: str,
    platform: str,
    locale: str,
    display_type: str,
    file_name: str,
    image_base64: str | None = None,
    image_path: str | None = None,
) -> dict[str, Any]:
    """Reserve → PUT the raw bytes → commit in one call.

    Supply either base64-encoded PNG/JPEG bytes or a local file path in a stdio deployment.
    Reuses an existing screenshot set for `display_type` if the locale already has one; creates
    one otherwise.
    """
    if not display_type.strip() or not display_type.startswith("APP_"):
        raise validation_error(
            "display_type must be a non-empty App Store Connect display type (for example APP_IPHONE_67)",
            field_name="display_type",
        )
    source = BinarySource.from_inputs(
        base64_value=image_base64, file_path=image_path, field_name="image"
    )
    suffix = f".{file_name.lower().rsplit('.', 1)[-1]}" if "." in file_name else ""
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise validation_error(
            f"screenshot file must use one of {sorted(SUPPORTED_IMAGE_SUFFIXES)}",
            field_name="file_name",
        )
    if png_has_alpha(source) is True:
        raise validation_error(
            "screenshots cannot contain an alpha channel or transparency", field_name="image"
        )

    version_localization_id = _resolve_version_localization_id(
        app_id, platform, locale, require_editable=True
    )
    client = context.get_asc_client()

    existing_sets = client.list_screenshot_sets(version_localization_id)
    matching = next((s for s in existing_sets if s.display_type == display_type), None)
    if matching is not None and len(matching.screenshot_ids) >= MAX_SCREENSHOTS_PER_SET:
        raise validation_error(
            "Apple allows at most 10 screenshots per device size; delete one before uploading another",
            field_name="display_type",
        )
    screenshot_set_id = matching.id if matching else client.create_screenshot_set(
        version_localization_id, display_type=display_type
    )

    reservation = client.reserve_screenshot(screenshot_set_id, file_name=file_name, file_size=source.size)
    screenshot_id = _require_screenshot_id(reservation)
    operations = (reservation.get("attributes") or {}).get("uploadOperations") or []
    try:
        upload_operations(source, operations, timeout=120.0)
        client.commit_screenshot(screenshot_id, checksum=source.checksum)
    except ASOgenicError:
        try:
            client.delete_screenshot(screenshot_id)
        except ASOgenicError:
            pass
        raise

    delivery: dict[str, Any] = {"state": None, "errors": [], "warnings": []}
    for attempt in range(_DELIVERY_POLL_ATTEMPTS):
        delivery = client.get_screenshot_delivery_state(screenshot_id)
        if delivery.get("state") in ("COMPLETE", "FAILED"):
            break
        if attempt < _DELIVERY_POLL_ATTEMPTS - 1:
            time.sleep(_DELIVERY_POLL_INTERVAL_S)

    result = {
        "screenshot_set_id": screenshot_set_id, "screenshot_id": screenshot_id, "display_type": display_type,
        "asset_delivery_state": delivery.get("state"),
    }
    if delivery.get("state") == "FAILED":
        raise asc_state_error(
            f"screenshot upload was accepted but Apple's asset pipeline rejected the image: "
            f"{delivery.get('errors')} (screenshot_id={screenshot_id})",
            code="STATE_ERROR",
            detail={"screenshot_id": screenshot_id, **delivery},
        )
    if delivery.get("state") not in ("COMPLETE",):
        result["note"] = (
            "Apple is still processing this asset; poll asogenic_get_screenshot_status(screenshot_id) "
            "to confirm it lands as COMPLETE before submitting for review."
        )
    return result


def get_screenshot_status(*, screenshot_id: str) -> dict[str, Any]:
    """Current Apple-side asset processing state for one uploaded screenshot — state
    (COMPLETE/FAILED/IN_PROGRESS/...), plus any errors/warnings Apple attached. Confirmed live
    this is necessary: a screenshot can commit successfully through asogenic_upload_screenshot
    and still be permanently rejected afterward (e.g. an alpha channel), invisible until a much
    later submission attempt surfaces it as unrelated-looking ASC error detail."""
    client = context.get_asc_client()
    return client.get_screenshot_delivery_state(screenshot_id)


def delete_screenshot(*, screenshot_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    client.delete_screenshot(screenshot_id)
    return {"deleted": True}


def reorder_screenshots(*, screenshot_set_id: str, screenshot_ids: list[str]) -> dict[str, Any]:
    if not screenshot_ids or len(screenshot_ids) > MAX_SCREENSHOTS_PER_SET:
        raise validation_error(
            "screenshot_ids must contain one to ten screenshot ids", field_name="screenshot_ids"
        )
    if len(set(screenshot_ids)) != len(screenshot_ids):
        raise validation_error("screenshot_ids must be unique", field_name="screenshot_ids")
    context.get_asc_client().replace_relationship(
        f"/v1/appScreenshotSets/{screenshot_set_id}/relationships/appScreenshots",
        data=[{"type": "appScreenshots", "id": value} for value in screenshot_ids],
    )
    return {"reordered": True, "screenshot_set_id": screenshot_set_id, "screenshot_ids": screenshot_ids}
