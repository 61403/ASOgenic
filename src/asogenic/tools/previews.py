"""App Preview video management for App Store version localizations.

Apple exposes previews as ``appPreviewSets`` plus ``appPreviews`` rather than as screenshots.
This module keeps that resource boundary explicit while reusing the common signed-upload helper.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from asogenic import context
from asogenic.errors import ASOgenicError, asc_state_error, validation_error
from asogenic.tools.media_io import BinarySource, upload_operations

MAX_PREVIEW_BYTES = 500 * 1024 * 1024
MAX_PREVIEWS_PER_SET = 3
SUPPORTED_VIDEO_SUFFIXES = frozenset({".mov", ".m4v", ".mp4"})


def _resolve_version_localization_id(app_id: str, platform: str, locale: str) -> str:
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version", code="NOT_FOUND")
    if not resolved.is_editable:
        raise asc_state_error(
            f"version for app '{app_id}' is not editable in its current state "
            f"({resolved.version_state or 'unknown'}); upload previews on an editable version",
            code="VERSION_NOT_EDITABLE",
        )
    locale_ids = client.list_app_store_version_localizations(resolved.version_id)
    ids = locale_ids.get(locale)
    if ids is None or ids.version_localization_id is None:
        raise asc_state_error(
            f"locale '{locale}' has no app store version localization yet",
            code="NOT_FOUND",
        )
    return ids.version_localization_id


def _preview_sets(version_localization_id: str) -> list[dict[str, Any]]:
    return context.get_asc_client().list_resource(
        f"/v1/appStoreVersionLocalizations/{version_localization_id}/appPreviewSets",
        params={"include": "appPreviews"},
    )


def list_previews(*, app_id: str, platform: str, locale: str) -> dict[str, Any]:
    # Listing remains useful for a version that is already live; only writes require editability.
    client = context.get_asc_client()
    resolved = client.resolve_app(app_id, platform=platform)
    if resolved.version_id is None:
        raise asc_state_error(f"app '{app_id}' has no app store version", code="NOT_FOUND")
    locale_ids = client.list_app_store_version_localizations(resolved.version_id)
    ids = locale_ids.get(locale)
    if ids is None or ids.version_localization_id is None:
        raise asc_state_error(f"locale '{locale}' has no app store version localization", code="NOT_FOUND")
    sets = _preview_sets(ids.version_localization_id)
    return {
        "preview_sets": [
            {
                "id": item.get("id"),
                "preview_type": (item.get("attributes") or {}).get("previewType"),
                "preview_ids": [
                    ref.get("id")
                    for ref in ((item.get("relationships") or {}).get("appPreviews") or {}).get("data", [])
                ],
            }
            for item in sets
        ]
    }


def _validate_preview_input(file_name: str, source: BinarySource, preview_type: str) -> None:
    if not preview_type.strip() or not preview_type.replace("_", "").isalnum():
        raise validation_error(
            "preview_type must be a non-empty App Store Connect preview type",
            field_name="preview_type",
        )
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_VIDEO_SUFFIXES:
        raise validation_error(
            f"preview file must use one of {sorted(SUPPORTED_VIDEO_SUFFIXES)}",
            field_name="file_name",
        )
    if source.size > MAX_PREVIEW_BYTES:
        raise validation_error(
            "preview exceeds Apple's 500 MB limit", field_name="file_name", limit=MAX_PREVIEW_BYTES
        )


def _normalize_preview_type(preview_type: str) -> str:
    """Accept legacy screenshot-style input while sending Apple's PreviewType value.

    Screenshot identifiers are prefixed with ``APP_``; preview identifiers are not. Early MCP
    clients used the screenshot spelling for both, which Apple rejects. Normalization keeps those
    callers working without freezing the actual PreviewType enum in ASOgenic.
    """
    return preview_type.strip().removeprefix("APP_")


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


def upload_preview(
    *,
    app_id: str,
    platform: str,
    locale: str,
    preview_type: str,
    file_name: str,
    preview_base64: str | None = None,
    preview_path: str | None = None,
    poster_frame_time_code: str | None = None,
) -> dict[str, Any]:
    preview_type = _normalize_preview_type(preview_type)
    source = BinarySource.from_inputs(
        base64_value=preview_base64, file_path=preview_path, field_name="preview"
    )
    _validate_preview_input(file_name, source, preview_type)
    if poster_frame_time_code is not None:
        _validate_poster_frame_time_code(poster_frame_time_code)

    version_localization_id = _resolve_version_localization_id(app_id, platform, locale)
    client = context.get_asc_client()
    sets = _preview_sets(version_localization_id)
    matching = next(
        (item for item in sets if (item.get("attributes") or {}).get("previewType") == preview_type),
        None,
    )
    if matching is None:
        set_body = client.create_resource(
            "appPreviewSets",
            attributes={"previewType": preview_type},
            relationships={
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": version_localization_id}
                }
            },
        )
        preview_set_id = _require_resource_id(set_body.get("data") or {}, resource="app preview set")
        existing_ids: list[str] = []
    else:
        preview_set_id = str(matching["id"])
        existing_ids = [
            str(ref["id"])
            for ref in ((matching.get("relationships") or {}).get("appPreviews") or {}).get("data", [])
        ]
    if len(existing_ids) >= MAX_PREVIEWS_PER_SET:
        raise validation_error(
            "an app preview set can contain at most three previews; reorder or remove one first",
            field_name="preview_type",
        )

    reserved = client.create_resource(
        "appPreviews",
        attributes={"fileName": file_name, "fileSize": source.size},
        relationships={"appPreviewSet": {"data": {"type": "appPreviewSets", "id": preview_set_id}}},
    )
    data = reserved.get("data") or {}
    preview_id = _require_resource_id(data, resource="app preview")
    operations = (data.get("attributes") or {}).get("uploadOperations") or []
    try:
        upload_operations(source, operations)
        attrs: dict[str, Any] = {"uploaded": True, "sourceFileChecksum": source.checksum}
        if poster_frame_time_code is not None:
            attrs["previewFrameTimeCode"] = poster_frame_time_code
        client.update_resource("appPreviews", preview_id, attributes=attrs)
    except ASOgenicError:
        try:
            client.delete_resource("appPreviews", preview_id)
        except ASOgenicError:
            pass
        raise
    return {
        "preview_set_id": preview_set_id,
        "preview_id": preview_id,
        "preview_type": preview_type,
        "video_delivery_state": None,
        "note": "Apple processes app previews asynchronously; poll asogenic_get_app_preview_status.",
    }


def get_preview_status(*, preview_id: str) -> dict[str, Any]:
    data = context.get_asc_client().get_resource(
        f"/v1/appPreviews/{preview_id}",
        params={"fields[appPreviews]": "assetDeliveryState,videoDeliveryState,previewFrameTimeCode"},
    ).get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "preview_id": preview_id,
        "asset_delivery_state": attrs.get("assetDeliveryState"),
        "video_delivery_state": attrs.get("videoDeliveryState"),
        "poster_frame_time_code": attrs.get("previewFrameTimeCode"),
    }


def set_preview_poster_frame(*, preview_id: str, poster_frame_time_code: str) -> dict[str, Any]:
    _validate_poster_frame_time_code(poster_frame_time_code)
    return context.get_asc_client().update_resource(
        "appPreviews", preview_id, attributes={"previewFrameTimeCode": poster_frame_time_code}
    )


def reorder_previews(*, preview_set_id: str, preview_ids: list[str]) -> dict[str, Any]:
    if not preview_ids or len(preview_ids) > MAX_PREVIEWS_PER_SET or len(set(preview_ids)) != len(preview_ids):
        raise validation_error(
            "preview_ids must contain one to three unique preview ids",
            field_name="preview_ids",
        )
    context.get_asc_client().replace_relationship(
        f"/v1/appPreviewSets/{preview_set_id}/relationships/appPreviews",
        data=[{"type": "appPreviews", "id": preview_id} for preview_id in preview_ids],
    )
    return {"reordered": True, "preview_set_id": preview_set_id, "preview_ids": preview_ids}


def delete_preview(*, preview_id: str) -> dict[str, Any]:
    context.get_asc_client().delete_resource("appPreviews", preview_id)
    return {"deleted": True, "preview_id": preview_id}
