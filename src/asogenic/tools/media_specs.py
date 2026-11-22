"""Current media capability discovery and conservative fallback specifications.

Apple evolves device classes more frequently than the MCP package can be released. The live
reference response is returned unchanged for callers that need Apple's exact identifiers. The
fallback only documents stable, high-level capabilities and is never used to claim an unknown
device slot is valid.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from asogenic import context

FALLBACK_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {"family": "screenshots", "kind": "image", "max_items_per_set": 10},
    {"family": "app_previews", "kind": "video", "max_items_per_set": 3, "max_bytes": 500 * 1024 * 1024, "min_seconds": 15, "max_seconds": 30},
    {"family": "creative_assets", "kind": "image_or_video", "placements": ["HEADER", "SEARCH_RESULTS"]},
)


def get_media_specs() -> dict[str, Any]:
    """Return Apple's reference data plus ASOgenic's stable capability envelope."""
    reference = context.get_asc_client().get_resource("/v1/appAssetLibraryRefData")
    return build_media_specs(reference)


def _reference_records(value: Any) -> Iterable[dict[str, Any]]:
    """Yield JSON:API-like records from either current or older ref-data shapes."""
    if isinstance(value, dict):
        if isinstance(value.get("data"), (list, dict)):
            yield from _reference_records(value["data"])
        if isinstance(value.get("included"), list):
            yield from _reference_records(value["included"])
        if isinstance(value.get("type"), str) and isinstance(value.get("attributes"), dict):
            yield value
        for key, child in value.items():
            if key not in {"data", "included", "attributes", "relationships"}:
                yield from _reference_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_records(child)


def _record_identifier(record: dict[str, Any]) -> str | None:
    attrs = record.get("attributes") or {}
    for key in ("displayType", "screenshotDisplayType", "previewType", "specId", "id", "shortName"):
        value = attrs.get(key) or record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def build_media_specs(reference: dict[str, Any]) -> dict[str, Any]:
    """Add a normalized, lossless index while retaining Apple's raw response."""
    records = list(_reference_records(reference))
    device_specs: list[dict[str, Any]] = []
    placement_specs: list[dict[str, Any]] = []
    required: list[str] = []
    for record in records:
        attrs = record.get("attributes") or {}
        identifier = _record_identifier(record)
        if identifier is None:
            continue
        item = {
            "id": record.get("id"),
            "type": record.get("type"),
            "identifier": identifier,
            "attributes": attrs,
        }
        lowered_type = str(record.get("type") or "").lower()
        if any(token in lowered_type for token in ("placement", "mapping")) or any(
            key in attrs for key in ("placementType", "placementGroup", "placements")
        ):
            placement_specs.append(item)
        else:
            device_specs.append(item)
        if attrs.get("required") is True or attrs.get("isRequired") is True:
            required.append(identifier)
    return {
        "source": "appAssetLibraryRefData",
        "capabilities": list(FALLBACK_CAPABILITIES),
        "reference_data": reference,
        "normalized": {"device_specs": device_specs, "placement_specs": placement_specs},
        "required_display_types": sorted(set(required)),
        "note": "Use reference_data or normalized identifiers for current device and placement choices; unknown values are not hardcoded by ASOgenic.",
    }
