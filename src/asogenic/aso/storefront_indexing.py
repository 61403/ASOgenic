"""The US keyword multiplier. Port of reference/swift/Services/ASO/StorefrontIndexing.swift.

Only the US row ships — an unverified row would silently donate a locale's keyword field to a
storefront that never reads it. Add a new row only after live confirmation, dated like
AppleStorefrontIDs.
"""

from __future__ import annotations

ADDITIONALLY_INDEXED_LOCALES: dict[str, list[str]] = {
    # Source: user's ASO research, recorded 2026-07-29. Not independently verified.
    "en-US": ["ar-SA", "es-MX", "fr-FR", "ru", "ko", "pt-BR", "vi", "zh-Hans", "zh-Hant"],
}


def donor_locales(*, for_primary: str) -> list[str]:
    return ADDITIONALLY_INDEXED_LOCALES.get(for_primary, [])


def primary_locale(*, for_donor: str) -> str | None:
    for primary, donors in ADDITIONALLY_INDEXED_LOCALES.items():
        if for_donor in donors:
            return primary
    return None


def supports_donation(*, primary_locale_id: str) -> bool:
    return len(donor_locales(for_primary=primary_locale_id)) > 0
