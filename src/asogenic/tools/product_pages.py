"""App Store product-page surfaces introduced/expanded in App Store Connect 2026.

Apple sources:
* Custom Product Pages: up to 70 targeted, localizable pages with keywords and deep links.
* Asset Library: appAssetLibraryImages/Videos, placements, ordering, and reference data.
* Product Page Optimization: v2 appStoreVersionExperiments and treatments.

The upload implementation follows Apple's four-step reservation/upload/commit/poll workflow.
The old screenshot tools remain available, but new media should use this module and the Asset
Library resources.
"""

from __future__ import annotations

import math
import re
import time
from typing import TYPE_CHECKING, Any

from asogenic import context
from asogenic.errors import ASOgenicError, asc_state_error, validation_error
from asogenic.tools.media_io import BinarySource, png_has_alpha, upload_operations

if TYPE_CHECKING:
    from asogenic.services.asc_client import ASCClient

ASSET_CATEGORIES = frozenset({"CREATIVE_ASSETS", "APP_SCREENSHOTS_AND_PREVIEWS"})
ASSET_KINDS = {"image": "appAssetLibraryImages", "video": "appAssetLibraryVideos"}
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
VIDEO_SUFFIXES = frozenset({".mov", ".m4v", ".mp4"})
LOCALIZATION_RELATIONSHIPS = {
    "appStoreVersionLocalizations": "appStoreVersionLocalization",
    "appCustomProductPageLocalizations": "appCustomProductPageLocalization",
    "appEventLocalizations": "appEventLocalization",
    "appStoreVersionExperimentTreatmentLocalizations": "appStoreVersionExperimentTreatmentLocalization",
}


def _client() -> ASCClient:
    return context.get_asc_client()


def _app_resource_id(app_id: str) -> str:
    """Accept either an ASC app id or a bundle id, matching the rest of ASOgenic."""
    if app_id.isdigit() or re.fullmatch(r"[0-9a-fA-F-]{20,}", app_id):
        return app_id
    return _client().find_app_by_bundle_id(app_id).id


def list_custom_product_pages(*, app_id: str) -> dict[str, Any]:
    return {"pages": _client().list_resource(f"/v1/apps/{_app_resource_id(app_id)}/appCustomProductPages")}


def create_custom_product_page(
    *, app_id: str, name: str, template_version_id: str | None = None,
    locale: str = "en-US", promotional_text: str = "",
) -> dict[str, Any]:
    relationships: dict[str, Any] = {"app": {"data": {"type": "apps", "id": _app_resource_id(app_id)}}}
    if template_version_id:
        relationships["appStoreVersionTemplate"] = {"data": {"type": "appStoreVersions", "id": template_version_id}}
    version_id = "${new-appCustomProductPageVersion-id}"
    localization_id = "${new-appCustomProductPageLocalization-id}"
    relationships["appCustomProductPageVersions"] = {
        "data": [{"type": "appCustomProductPageVersions", "id": version_id}]
    }
    included = [
        {
            "type": "appCustomProductPageVersions",
            "id": version_id,
            "relationships": {
                "appCustomProductPage": {},
                "appCustomProductPageLocalizations": {
                    "data": [{"type": "appCustomProductPageLocalizations", "id": localization_id}]
                },
            },
        },
        {
            "type": "appCustomProductPageLocalizations",
            "id": localization_id,
            "attributes": {"locale": locale, **({"promotionalText": promotional_text} if promotional_text else {})},
        },
    ]
    return _client().create_resource(
        "appCustomProductPages", attributes={"name": name}, relationships=relationships, included=included
    )


def update_custom_product_page(*, page_id: str, visible: bool | None = None, name: str | None = None) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if visible is not None:
        attrs["visible"] = visible
    if name is not None:
        attrs["name"] = name
    if not attrs:
        raise validation_error("provide visible or name", field_name="page_id")
    return _client().update_resource("appCustomProductPages", page_id, attributes=attrs)


def delete_custom_product_page(*, page_id: str) -> dict[str, Any]:
    _client().delete_resource("appCustomProductPages", page_id)
    return {"deleted": True, "page_id": page_id}


def create_custom_product_page_version(*, page_id: str, deep_link: str | None = None) -> dict[str, Any]:
    relationships = {"appCustomProductPage": {"data": {"type": "appCustomProductPages", "id": page_id}}}
    attrs = {"deepLink": deep_link} if deep_link else {}
    return _client().create_resource("appCustomProductPageVersions", attributes=attrs, relationships=relationships)


def list_custom_product_page_localizations(*, version_id: str) -> dict[str, Any]:
    path = f"/v1/appCustomProductPageVersions/{version_id}/appCustomProductPageLocalizations"
    return {"localizations": _client().list_resource(path)}


def delete_custom_product_page_localization(*, localization_id: str) -> dict[str, Any]:
    _client().delete_resource("appCustomProductPageLocalizations", localization_id)
    return {"deleted": True, "localization_id": localization_id}


def create_custom_product_page_localization(*, version_id: str, locale: str, promotional_text: str = "") -> dict[str, Any]:
    attrs: dict[str, Any] = {"locale": locale}
    if promotional_text:
        attrs["promotionalText"] = promotional_text
    relationships = {"appCustomProductPageVersion": {"data": {"type": "appCustomProductPageVersions", "id": version_id}}}
    return _client().create_resource("appCustomProductPageLocalizations", attributes=attrs, relationships=relationships)


def update_custom_product_page_localization(*, localization_id: str, promotional_text: str) -> dict[str, Any]:
    return _client().update_resource("appCustomProductPageLocalizations", localization_id, attributes={"promotionalText": promotional_text})


def set_custom_page_keywords(*, localization_id: str, keyword_ids: list[str]) -> dict[str, Any]:
    data = [{"type": "appKeywords", "id": value} for value in keyword_ids]
    return _client().create_relationship(
        f"/v1/appCustomProductPageLocalizations/{localization_id}/relationships/searchKeywords",
        data=data,
    )


def list_custom_page_keywords(*, localization_id: str) -> dict[str, Any]:
    path = f"/v1/appCustomProductPageLocalizations/{localization_id}/searchKeywords"
    return {"keywords": _client().list_resource(path)}


def remove_custom_page_keywords(*, localization_id: str, keyword_ids: list[str]) -> dict[str, Any]:
    data = [{"type": "appKeywords", "id": value} for value in keyword_ids]
    _client().delete_relationship(
        f"/v1/appCustomProductPageLocalizations/{localization_id}/relationships/searchKeywords",
        data=data,
    )
    return {"removed": keyword_ids}


def list_asset_library(*, app_id: str) -> dict[str, Any]:
    return _client().get_resource(f"/v1/apps/{_app_resource_id(app_id)}/assetLibrary")


def list_asset_library_assets(*, library_id: str, kind: str, category: str | None = None,
                              state: str | None = None, spec_id: str | None = None) -> dict[str, Any]:
    resource = ASSET_KINDS.get(kind)
    if resource is None:
        raise validation_error("kind must be 'image' or 'video'", field_name="kind")
    params: dict[str, Any] = {}
    if category:
        params["filter[category]"] = category
    if state:
        params["filter[state]"] = state
    if spec_id:
        params["filter[specId]"] = spec_id
    return {"assets": _client().list_resource(f"/v1/appAssetLibraries/{library_id}/{kind}s", params=params)}


def get_asset_library_reference_data() -> dict[str, Any]:
    return _client().get_resource("/v1/appAssetLibraryRefData")


def _require_resource_id(data: dict[str, Any], *, resource: str) -> str:
    resource_id = data.get("id")
    if not isinstance(resource_id, str) or not resource_id:
        raise asc_state_error(
            f"Apple did not return an id while creating {resource}",
            code="MALFORMED_RESPONSE",
            detail=data,
        )
    return resource_id


def _validate_poster_frame_time_code(poster_frame_time_code: str) -> None:
    if isinstance(poster_frame_time_code, str):
        match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}):(\d{2})", poster_frame_time_code)
        if match is not None:
            _hours, minutes, seconds, frames = (int(value) for value in match.groups())
            if minutes < 60 and seconds < 60 and frames < 60:
                return
    try:
        value = float(poster_frame_time_code)
    except (TypeError, ValueError) as exc:
        raise validation_error(
            "poster_frame_time_code must be decimal seconds or HH:MM:SS:FF",
            field_name="poster_frame_time_code",
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise validation_error(
            "poster_frame_time_code must be decimal seconds or HH:MM:SS:FF",
            field_name="poster_frame_time_code",
        )


def _require_localization_type(localization_type: str) -> str:
    relationship = LOCALIZATION_RELATIONSHIPS.get(localization_type)
    if relationship is None:
        raise validation_error("unsupported localization_type", field_name="localization_type")
    return relationship


def upload_asset_library_asset(*, library_id: str, kind: str, category: str, file_name: str,
                               asset_base64: str | None = None, asset_path: str | None = None,
                               reference_name: str | None = None) -> dict[str, Any]:
    resource = ASSET_KINDS.get(kind)
    if resource is None:
        raise validation_error("kind must be 'image' or 'video'", field_name="kind")
    if category not in ASSET_CATEGORIES:
        raise validation_error(f"category must be one of {sorted(ASSET_CATEGORIES)}", field_name="category")
    suffix = f".{file_name.lower().rsplit('.', 1)[-1]}" if "." in file_name else ""
    allowed_suffixes = IMAGE_SUFFIXES if kind == "image" else VIDEO_SUFFIXES
    if suffix not in allowed_suffixes:
        raise validation_error(
            f"{kind} file must use one of {sorted(allowed_suffixes)}", field_name="file_name"
        )
    source = BinarySource.from_inputs(
        base64_value=asset_base64, file_path=asset_path, field_name="asset"
    )
    if kind == "image" and png_has_alpha(source) is True:
        raise validation_error(
            "App Store creative images cannot contain an alpha channel or transparency",
            field_name="asset",
        )
    attrs = {"fileName": file_name, "fileSize": source.size, "category": category}
    if reference_name:
        # Asset Library accepts the display name when reserving the upload.  The
        # later commit endpoint only accepts ``uploaded`` (unlike legacy
        # appScreenshots, it does not accept sourceFileChecksum).
        attrs["referenceName"] = reference_name
    relationships = {"assetLibrary": {"data": {"type": "appAssetLibraries", "id": library_id}}}
    client = _client()
    body = client.create_resource(resource, attributes=attrs, relationships=relationships)
    data = body.get("data", {})
    asset_id = _require_resource_id(data, resource="Asset Library asset")
    operations = (data.get("attributes") or {}).get("uploadOperations") or []
    try:
        upload_operations(source, operations)
        client.update_resource(resource, asset_id, attributes={"uploaded": True})
    except ASOgenicError:
        try:
            client.delete_resource(resource, asset_id)
        except ASOgenicError:
            pass
        raise
    result: dict[str, Any] = {"asset": {}}
    for attempt in range(5):
        asset_data = client.get_resource(f"/v1/{resource}/{asset_id}").get("data")
        if not isinstance(asset_data, dict):
            raise asc_state_error(
                "Apple returned a malformed Asset Library asset response",
                code="MALFORMED_RESPONSE",
                detail={"asset_id": asset_id, "response_data": asset_data},
            )
        result["asset"] = asset_data
        raw_attrs = asset_data.get("attributes")
        attrs = raw_attrs if isinstance(raw_attrs, dict) else {}
        raw_delivery = attrs.get("assetDeliveryState")
        delivery = raw_delivery if isinstance(raw_delivery, dict) else {}
        state = attrs.get("state") or delivery.get("state")
        if state in {"COMPLETE", "PREPARE_FOR_SUBMISSION", "APPROVED", "FAILED", "REJECTED", "ARCHIVED"} or attempt == 4:
            break
        time.sleep(2)
    final_raw_attrs = result["asset"].get("attributes")
    final_attrs = final_raw_attrs if isinstance(final_raw_attrs, dict) else {}
    final_raw_delivery = final_attrs.get("assetDeliveryState")
    final_delivery = final_raw_delivery if isinstance(final_raw_delivery, dict) else {}
    final_state = final_attrs.get("state") or final_delivery.get("state")
    if final_state in {"FAILED", "REJECTED"}:
        raise asc_state_error("Apple rejected the Asset Library asset", code="STATE_ERROR", detail=result)
    return result


def update_asset_library_asset(*, kind: str, asset_id: str, reference_name: str | None = None,
                               archived: bool | None = None, poster_frame_time_code: str | None = None) -> dict[str, Any]:
    resource = ASSET_KINDS.get(kind)
    if resource is None:
        raise validation_error("kind must be 'image' or 'video'", field_name="kind")
    attrs: dict[str, Any] = {}
    if reference_name is not None: attrs["referenceName"] = reference_name
    if archived is not None: attrs["archived"] = archived
    if poster_frame_time_code is not None: attrs["previewFrameTimeCode"] = poster_frame_time_code
    if not attrs:
        raise validation_error("provide reference_name, archived, or poster_frame_time_code", field_name="asset_id")
    if poster_frame_time_code is not None:
        _validate_poster_frame_time_code(poster_frame_time_code)
    return _client().update_resource(resource, asset_id, attributes=attrs)


def get_asset_library_asset_status(*, kind: str, asset_id: str) -> dict[str, Any]:
    resource = ASSET_KINDS.get(kind)
    if resource is None:
        raise validation_error("kind must be 'image' or 'video'", field_name="kind")
    data = _client().get_resource(f"/v1/{resource}/{asset_id}").get("data") or {}
    attrs = data.get("attributes") or {}
    delivery = attrs.get("assetDeliveryState") or {}
    relationships = data.get("relationships") or {}
    review_ref = (relationships.get("reviewSubmission") or {}).get("data") or {}
    return {
        "asset_id": asset_id,
        "kind": kind,
        "state": attrs.get("state") or delivery.get("state"),
        "asset_delivery_state": delivery,
        "state_details": attrs.get("stateDetails"),
        "spec_id": attrs.get("specId"),
        "review_submission_id": review_ref.get("id"),
    }


def delete_asset_library_asset(*, kind: str, asset_id: str) -> dict[str, Any]:
    resource = ASSET_KINDS.get(kind)
    if resource is None:
        raise validation_error("kind must be 'image' or 'video'", field_name="kind")
    current = get_asset_library_asset_status(kind=kind, asset_id=asset_id)
    if str(current.get("state") or "").upper() in {
        "READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "IN_REVIEW", "ACCEPTED", "APPROVED"
    }:
        raise asc_state_error(
            "submitted or approved Asset Library assets cannot be deleted; archive or remove the asset from its submission first",
            code="ASSET_NOT_DELETABLE",
            detail=current,
        )
    _client().delete_resource(resource, asset_id)
    return {"deleted": True, "asset_id": asset_id}


def list_placements(*, localization_id: str, localization_type: str = "appStoreVersionLocalizations") -> dict[str, Any]:
    _require_localization_type(localization_type)
    return {"placements": _client().list_resource(f"/v1/{localization_type}/{localization_id}/placements", params={"sort": "placementGroupPosition"})}


def create_placement(*, asset_id: str, asset_kind: str, placement_type: str, placement_group: str,
                     localization_id: str, localization_type: str = "appStoreVersionLocalizations") -> dict[str, Any]:
    resource = ASSET_KINDS.get(asset_kind)
    if resource is None:
        raise validation_error("asset_kind must be 'image' or 'video'", field_name="asset_kind")
    for field_name, value in (("asset_id", asset_id), ("placement_type", placement_type), ("placement_group", placement_group), ("localization_id", localization_id)):
        if not value.strip():
            raise validation_error(f"{field_name} must not be empty", field_name=field_name)
    media_relationship = "image" if asset_kind == "image" else "video"
    target_relationship = _require_localization_type(localization_type)
    relationships = {
        media_relationship: {"data": {"type": resource, "id": asset_id}},
        target_relationship: {"data": {"type": localization_type, "id": localization_id}},
    }
    return _client().create_resource("appAssetLibraryPlacements", attributes={"placementType": placement_type, "placementGroup": placement_group}, relationships=relationships)


def delete_placement(*, placement_id: str) -> dict[str, Any]:
    _client().delete_resource("appAssetLibraryPlacements", placement_id)
    return {"deleted": True, "placement_id": placement_id}


def reorder_placements(
    *, localization_id: str, placement_group: str, placement_ids: list[str],
    localization_type: str = "appStoreVersionLocalizations",
) -> dict[str, Any]:
    """Replace one Asset Library placement group's order for a localization."""
    target_relationship = _require_localization_type(localization_type)
    if not localization_id.strip():
        raise validation_error("localization_id must not be empty", field_name="localization_id")
    if not placement_group.strip():
        raise validation_error("placement_group must not be empty", field_name="placement_group")
    if not placement_ids:
        raise validation_error("placement_ids must contain at least one placement id", field_name="placement_ids")
    if any(not isinstance(value, str) or not value.strip() for value in placement_ids):
        raise validation_error("placement_ids must contain non-empty strings", field_name="placement_ids")
    if len(set(placement_ids)) != len(placement_ids):
        raise validation_error("placement_ids must be unique", field_name="placement_ids")
    return _client().create_resource(
        "appAssetLibraryPlacementOrderingRequests",
        attributes={"placementGroup": placement_group},
        relationships={
            "orderedPlacements": {
                "data": [
                    {"type": "appAssetLibraryPlacements", "id": placement_id}
                    for placement_id in placement_ids
                ]
            },
            target_relationship: {
                "data": {"type": localization_type, "id": localization_id}
            },
        },
    )


def list_optimization_tests(*, app_id: str, state: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"include": "latestControlVersion,appStoreVersionExperimentTreatments"}
    if state: params["filter[state]"] = state
    return {"tests": _client().list_resource(f"/v1/apps/{_app_resource_id(app_id)}/appStoreVersionExperimentsV2", params=params)}


def list_optimization_treatments(*, test_id: str) -> dict[str, Any]:
    if not test_id.strip():
        raise validation_error("test_id must not be empty", field_name="test_id")
    params = {
        "include": "appStoreVersionExperimentTreatmentLocalizations",
        "fields[appStoreVersionExperimentTreatments]": "name,appIcon,appIconName,promotedDate,appStoreVersionExperimentTreatmentLocalizations",
        "fields[appStoreVersionExperimentTreatmentLocalizations]": "locale,appScreenshotSets,appPreviewSets",
    }
    return {
        "treatments": _client().list_resource(
            f"/v2/appStoreVersionExperiments/{test_id}/appStoreVersionExperimentTreatments",
            params=params,
        )
    }


def list_treatment_localizations(*, treatment_id: str, locale: str | None = None) -> dict[str, Any]:
    if not treatment_id.strip():
        raise validation_error("treatment_id must not be empty", field_name="treatment_id")
    params: dict[str, Any] = {
        "include": "appScreenshotSets,appPreviewSets,placements",
        "fields[appStoreVersionExperimentTreatmentLocalizations]": "locale,appScreenshotSets,appPreviewSets,placements",
    }
    if locale is not None:
        if not locale.strip():
            raise validation_error("locale must not be empty", field_name="locale")
        params["filter[locale]"] = locale
    return {
        "localizations": _client().list_resource(
            f"/v1/appStoreVersionExperimentTreatments/{treatment_id}/appStoreVersionExperimentTreatmentLocalizations",
            params=params,
        )
    }


def create_treatment_localization(*, treatment_id: str, locale: str) -> dict[str, Any]:
    treatment_id = treatment_id.strip()
    locale = locale.strip()
    if not treatment_id:
        raise validation_error("treatment_id must not be empty", field_name="treatment_id")
    if not locale:
        raise validation_error("locale must not be empty", field_name="locale")
    existing = list_treatment_localizations(treatment_id=treatment_id, locale=locale)["localizations"]
    if existing:
        return {"localization": existing[0], "created": False}
    try:
        body = _client().create_resource(
            "appStoreVersionExperimentTreatmentLocalizations",
            attributes={"locale": locale},
            relationships={
                "appStoreVersionExperimentTreatment": {
                    "data": {"type": "appStoreVersionExperimentTreatments", "id": treatment_id}
                }
            },
        )
    except ASOgenicError as exc:
        # Two callers can race after the read above. Apple reports the second create as a
        # conflict; re-read so the public operation remains idempotent.
        if exc.detail.get("status_code") != 409:
            raise
        existing = list_treatment_localizations(treatment_id=treatment_id, locale=locale)["localizations"]
        if existing:
            return {"localization": existing[0], "created": False}
        raise
    return {"localization": body.get("data") or {}, "created": True}


def _ensure_treatment_localization(*, treatment_id: str, locale: str) -> dict[str, Any]:
    result = create_treatment_localization(treatment_id=treatment_id, locale=locale)
    localization = result.get("localization") or {}
    if not isinstance(localization.get("id"), str) or not localization["id"]:
        raise asc_state_error(
            "Apple did not return an id for the PPO treatment localization",
            code="MALFORMED_RESPONSE",
            detail=result,
        )
    return localization


def upload_ppo_treatment_asset(
    *, treatment_id: str, locale: str, library_id: str, kind: str, category: str,
    file_name: str, placement_type: str, placement_group: str,
    asset_base64: str | None = None, asset_path: str | None = None,
    reference_name: str | None = None, replace_placement_id: str | None = None,
) -> dict[str, Any]:
    """Upload one Asset Library asset and place it on exactly one PPO treatment locale.

    When ``replace_placement_id`` is supplied, the old placement is deleted before the new one
    is created so Apple's placement-group limit is never exceeded. The new placement is then
    reordered into the old position. Default-page localizations are never touched because the
    target relationship is fixed to treatment localizations here.
    """
    for field_name, value in (
        ("treatment_id", treatment_id), ("locale", locale), ("library_id", library_id),
        ("placement_type", placement_type), ("placement_group", placement_group),
    ):
        if not value.strip():
            raise validation_error(f"{field_name} must not be empty", field_name=field_name)
    if replace_placement_id is not None and not replace_placement_id.strip():
        raise validation_error("replace_placement_id must not be empty", field_name="replace_placement_id")

    localization = _ensure_treatment_localization(treatment_id=treatment_id, locale=locale)
    localization_id = localization["id"]
    replacement_order: list[str] | None = None
    if replace_placement_id is not None:
        existing = list_placements(
            localization_id=localization_id,
            localization_type="appStoreVersionExperimentTreatmentLocalizations",
        )["placements"]
        old_placement = next(
            (item for item in existing if isinstance(item, dict) and item.get("id") == replace_placement_id),
            None,
        )
        if old_placement is None:
            raise asc_state_error(
                f"placement '{replace_placement_id}' is not attached to treatment '{treatment_id}' locale '{locale}'",
                code="NOT_FOUND",
            )
        raw_old_attrs = old_placement.get("attributes")
        old_attrs = raw_old_attrs if isinstance(raw_old_attrs, dict) else {}
        if old_attrs.get("placementType") not in (None, placement_type) or old_attrs.get("placementGroup") not in (None, placement_group):
            raise validation_error(
                "replace_placement_id must refer to the same placement type and group",
                field_name="replace_placement_id",
            )
        group_placements = [
            item for item in existing
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and (
                not isinstance(item.get("attributes"), dict)
                or item["attributes"].get("placementGroup") in (None, placement_group)
            )
        ]
        replacement_order = [item["id"] for item in group_placements]
        try:
            old_index = replacement_order.index(replace_placement_id)
        except ValueError as exc:
            raise asc_state_error(
                "Apple returned a replacement placement without a usable placement-group order",
                code="MALFORMED_RESPONSE",
                detail={"placement_id": replace_placement_id, "placement_group": placement_group},
            ) from exc

    uploaded = upload_asset_library_asset(
        library_id=library_id, kind=kind, category=category, file_name=file_name,
        asset_base64=asset_base64, asset_path=asset_path, reference_name=reference_name,
    )
    asset = uploaded.get("asset") or {}
    asset_id = _require_resource_id(asset, resource="Asset Library asset")
    if replace_placement_id is not None:
        try:
            delete_placement(placement_id=replace_placement_id)
        except ASOgenicError as exc:
            raise asc_state_error(
                "old PPO treatment placement could not be removed; no replacement was created",
                code="PARTIAL_REPLACEMENT",
                detail={
                    "asset_id": asset_id,
                    "old_placement_id": replace_placement_id,
                    "delete_error": exc.to_dict(),
                },
            ) from exc
    try:
        placement = create_placement(
            asset_id=asset_id, asset_kind=kind, placement_type=placement_type,
            placement_group=placement_group, localization_id=localization_id,
            localization_type="appStoreVersionExperimentTreatmentLocalizations",
        )
    except ASOgenicError as exc:
        if replace_placement_id is None:
            raise
        raise asc_state_error(
            "old PPO treatment placement was removed, but the replacement could not be created",
            code="PARTIAL_REPLACEMENT",
            detail={
                "asset_id": asset_id,
                "old_placement_id": replace_placement_id,
                "create_error": exc.to_dict(),
            },
        ) from exc
    if isinstance(placement, dict):
        placement_data = placement.get("data") or placement
    else:
        placement_data = {}
    try:
        new_placement_id = _require_resource_id(placement_data, resource="PPO treatment placement")
    except ASOgenicError as exc:
        if replace_placement_id is None:
            raise
        raise asc_state_error(
            "old PPO treatment placement was removed, but Apple returned no usable replacement",
            code="PARTIAL_REPLACEMENT",
            detail={
                "asset_id": asset_id,
                "old_placement_id": replace_placement_id,
                "placement_response": placement,
                "create_error": exc.to_dict(),
            },
        ) from exc
    if replacement_order is not None:
        replacement_order[old_index] = new_placement_id
        try:
            reorder_placements(
                localization_id=localization_id,
                placement_group=placement_group,
                placement_ids=replacement_order,
                localization_type="appStoreVersionExperimentTreatmentLocalizations",
            )
        except ASOgenicError as exc:
            raise asc_state_error(
                "PPO treatment replacement was created, but its original placement order could not be restored",
                code="PARTIAL_REPLACEMENT",
                detail={
                    "asset_id": asset_id,
                    "new_placement_id": new_placement_id,
                    "old_placement_id": replace_placement_id,
                    "reorder_error": exc.to_dict(),
                },
            ) from exc
    return {
        "treatment_id": treatment_id,
        "locale": locale,
        "localization_id": localization_id,
        "asset": asset,
        "placement": placement_data,
        "replaced_placement_id": replace_placement_id,
    }


def create_optimization_test(*, app_id: str, name: str, platform: str, traffic_proportion: int = 25) -> dict[str, Any]:
    if not 1 <= traffic_proportion <= 100:
        raise validation_error("traffic_proportion must be between 1 and 100", field_name="traffic_proportion")
    relationships = {"app": {"data": {"type": "apps", "id": _app_resource_id(app_id)}}}
    return _client().create_resource("appStoreVersionExperiments", attributes={"name": name, "platform": platform, "trafficProportion": traffic_proportion}, relationships=relationships, version="v2")


def update_optimization_test(*, test_id: str, started: bool | None = None, traffic_proportion: int | None = None) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if started is not None: attrs["started"] = started
    if traffic_proportion is not None: attrs["trafficProportion"] = traffic_proportion
    if not attrs: raise validation_error("provide started or traffic_proportion", field_name="test_id")
    return _client().update_resource("appStoreVersionExperiments", test_id, attributes=attrs, version="v2")
