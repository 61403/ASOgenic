"""Apple's internal numeric storefront IDs, keyed by ISO 3166-1 alpha-2 country code.

Port of reference/swift/Services/ASO/AppleStorefrontIDs.swift — port verbatim, do not add
unverified rows. Every entry was confirmed against the live autocomplete endpoint; a wrong ID is
indistinguishable from "no results", so an unverified guess degrades research silently instead of
failing loudly. Deliberately absent: BD (Bangladesh) — probed 2026-07-29, returned empty.
"""

from __future__ import annotations

STOREFRONT_IDS_BY_COUNTRY_CODE: dict[str, int] = {
    "US": 143_441, "GB": 143_444, "FR": 143_442, "DE": 143_443, "CA": 143_455,
    "AU": 143_460, "JP": 143_462, "IT": 143_450, "ES": 143_454, "NL": 143_452,
    "BR": 143_503, "MX": 143_468, "CN": 143_465, "KR": 143_466, "RU": 143_469,
    "IN": 143_467, "SE": 143_456, "CH": 143_459, "BE": 143_446, "AT": 143_445,
    "IE": 143_449, "PT": 143_453, "PL": 143_478, "TR": 143_480, "HK": 143_463,
    "TW": 143_470, "SG": 143_464, "NZ": 143_461, "DK": 143_458, "NO": 143_457,
    "FI": 143_447, "GR": 143_448, "CZ": 143_489, "HU": 143_482, "RO": 143_487,
    "ZA": 143_472, "IL": 143_491, "AE": 143_481, "SA": 143_479, "TH": 143_475,
    "MY": 143_473, "PH": 143_474, "ID": 143_476, "VN": 143_471,
    "HR": 143_494, "SK": 143_496, "SI": 143_499, "UA": 143_492, "PK": 143_477,
}


def storefront_id_for_country(code: str) -> int | None:
    return STOREFRONT_IDS_BY_COUNTRY_CODE.get(code.upper())
