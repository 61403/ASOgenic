"""asogenic_get_field_spec. Source: docs/interfaces/data-model.md's limits table and
docs/use-cases/02-source-fetch-and-guidance.md's content guidance. Pure — no ASC dependency.
"""

from __future__ import annotations

from typing import Any

from asogenic.models.locales import is_valid_locale
from asogenic.models.metadata import FIELD_LIMITS, MetadataField

BARRED_CONTENT_RULES = [
    "no invented ratings",
    "no invented awards",
    "no invented download counts",
    "no invented testimonials or press quotes",
    "no features not already present in the source content",
]

# Never AI-generated: ported from the source as-is.
_NON_GENERABLE_URL_FIELDS = ("marketing_url", "support_url", "privacy_policy_url")

_FIELD_ATTR_NAMES = {
    MetadataField.NAME: "name",
    MetadataField.SUBTITLE: "subtitle",
    MetadataField.DESCRIPTION: "description",
    MetadataField.KEYWORDS: "keywords",
    MetadataField.PROMOTIONAL_TEXT: "promotional_text",
    MetadataField.WHATS_NEW: "whats_new",
}


def get_field_spec(*, locale: str, fields: list[str] | None = None) -> dict[str, Any]:
    """Per-field {limit, is_byte_limit, generable, barred_rules}. `fields` optionally restricts
    to a subset of field names (defaults to every field, including the never-generated URLs)."""
    if not is_valid_locale(locale):
        raise ValueError(f"unknown locale '{locale}'")

    spec: dict[str, dict[str, Any]] = {}
    for field, attr in _FIELD_ATTR_NAMES.items():
        limit = FIELD_LIMITS[field]
        spec[attr] = {
            "limit": limit.limit,
            "is_byte_limit": limit.is_byte_limit,
            "generable": True,
            "barred_rules": BARRED_CONTENT_RULES,
        }
    for attr in _NON_GENERABLE_URL_FIELDS:
        spec[attr] = {"limit": None, "is_byte_limit": False, "generable": False, "barred_rules": []}

    if fields is not None:
        requested = set(fields)
        spec = {name: value for name, value in spec.items() if name in requested}

    return {"locale": locale, "fields": spec}
