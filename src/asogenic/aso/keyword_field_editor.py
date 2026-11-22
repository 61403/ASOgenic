"""Insert/remove a single term in a keyword field under the same rules MetadataAssembler applies.
Port of reference/swift/Services/ASO/KeywordFieldEditor.swift.

Rejects with a reason rather than silently clamping — a `+` that does nothing is a bug report;
one that explains "already in your title" teaches the model the field is one indexed pool.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from asogenic.aso.listing_tokenizer import is_platform_term
from asogenic.aso.listing_tokenizer import normalise as _normalise
from asogenic.aso.metadata_assembler import KEYWORD_BYTE_LIMIT, violates_style
from asogenic.aso.storefront_catalog import KeywordFieldStyle


class Rejection(enum.Enum):
    ALREADY_PRESENT = "alreadyPresent"
    INDEXED_IN_NAME_OR_SUBTITLE = "indexedInNameOrSubtitle"
    NO_ROOM = "noRoom"
    PLATFORM_WORD = "platformWord"
    UNUSABLE = "unusable"
    BRAND_TERM = "brandTerm"


@dataclass(frozen=True)
class Inserted:
    field: str


@dataclass(frozen=True)
class Rejected:
    reason: Rejection


InsertOutcome = Inserted | Rejected


def terms(field: str) -> list[str]:
    """The terms in a field, in order, with empty entries dropped."""
    return [t.strip() for t in field.split(",") if t.strip()]


def keys(field: str) -> set[str]:
    return {_normalise(t) for t in terms(field)}


def byte_count(field: str) -> int:
    return len(field.encode("utf-8"))


def _has_letter(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _is_usable(
    term: str, *, style: KeywordFieldStyle, is_brand_term: bool, is_autocomplete_confirmed: bool
) -> bool:
    if not _has_letter(term):
        return False
    if "," in term:
        return False
    return not violates_style(
        term, style=style, is_brand_term=is_brand_term, is_autocomplete_confirmed=is_autocomplete_confirmed
    )


def insert(
    term: str,
    *,
    field: str,
    style: KeywordFieldStyle,
    covered_elsewhere: frozenset[str] = frozenset(),
    is_brand_term: bool = False,
    is_autocomplete_confirmed: bool = False,
) -> InsertOutcome:
    trimmed = term.strip()

    if is_brand_term:
        return Rejected(Rejection.BRAND_TERM)
    if is_platform_term(trimmed):
        return Rejected(Rejection.PLATFORM_WORD)
    if not _is_usable(
        trimmed, style=style, is_brand_term=is_brand_term, is_autocomplete_confirmed=is_autocomplete_confirmed
    ):
        return Rejected(Rejection.UNUSABLE)

    key = _normalise(trimmed)
    if key in keys(field):
        return Rejected(Rejection.ALREADY_PRESENT)
    if key in covered_elsewhere:
        return Rejected(Rejection.INDEXED_IN_NAME_OR_SUBTITLE)

    candidate = ",".join([*terms(field), trimmed])
    if byte_count(candidate) > KEYWORD_BYTE_LIMIT:
        return Rejected(Rejection.NO_ROOM)
    return Inserted(candidate)


def remove(term: str, *, field: str) -> str:
    key = _normalise(term)
    kept = [t for t in terms(field) if _normalise(t) != key]
    return ",".join(kept)
