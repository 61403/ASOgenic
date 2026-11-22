"""MetadataLocalization and the field/limit tables. Source: docs/interfaces/data-model.md,
reference/swift/Models/MetadataLocalization.swift."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class MetadataField(enum.Enum):
    NAME = "name"
    SUBTITLE = "subtitle"
    DESCRIPTION = "description"
    KEYWORDS = "keywords"
    PROMOTIONAL_TEXT = "promotionalText"
    WHATS_NEW = "whatsNew"


TRANSLATABLE = [
    MetadataField.NAME,
    MetadataField.SUBTITLE,
    MetadataField.DESCRIPTION,
    MetadataField.KEYWORDS,
    MetadataField.PROMOTIONAL_TEXT,
    MetadataField.WHATS_NEW,
]


@dataclass(frozen=True)
class _FieldLimit:
    limit: int
    is_byte_limit: bool


FIELD_LIMITS: dict[MetadataField, _FieldLimit] = {
    MetadataField.NAME: _FieldLimit(30, False),
    MetadataField.SUBTITLE: _FieldLimit(30, False),
    MetadataField.DESCRIPTION: _FieldLimit(4000, False),
    MetadataField.PROMOTIONAL_TEXT: _FieldLimit(170, False),
    MetadataField.WHATS_NEW: _FieldLimit(4000, False),
    MetadataField.KEYWORDS: _FieldLimit(100, True),
}


class MetadataFieldGroup(enum.Enum):
    APP_IDENTITY = "app_identity"
    KEYWORDS = "keywords"
    STORE_LISTING = "store_listing"
    WHATS_NEW = "whats_new"


FIELD_GROUP_FIELDS: dict[MetadataFieldGroup, list[MetadataField]] = {
    MetadataFieldGroup.APP_IDENTITY: [MetadataField.NAME, MetadataField.SUBTITLE],
    MetadataFieldGroup.KEYWORDS: [MetadataField.KEYWORDS],
    MetadataFieldGroup.STORE_LISTING: [MetadataField.DESCRIPTION, MetadataField.PROMOTIONAL_TEXT],
    MetadataFieldGroup.WHATS_NEW: [MetadataField.WHATS_NEW],
}

FIELD_GROUP_SUPPORTS_OPTIMIZE: dict[MetadataFieldGroup, bool] = {
    MetadataFieldGroup.APP_IDENTITY: True,
    MetadataFieldGroup.KEYWORDS: True,
    MetadataFieldGroup.STORE_LISTING: False,
    MetadataFieldGroup.WHATS_NEW: False,
}


def measured_length(field_: MetadataField, value: str) -> int:
    limit = FIELD_LIMITS[field_]
    return len(value.encode("utf-8")) if limit.is_byte_limit else len(value)


_FIELD_ATTR = {
    MetadataField.NAME: "name",
    MetadataField.SUBTITLE: "subtitle",
    MetadataField.DESCRIPTION: "description",
    MetadataField.KEYWORDS: "keywords",
    MetadataField.PROMOTIONAL_TEXT: "promotional_text",
    MetadataField.WHATS_NEW: "whats_new",
}


@dataclass
class MetadataLocalization:
    name: str = ""
    subtitle: str = ""
    keywords: str = ""
    promotional_text: str = ""
    description: str = ""
    whats_new: str = ""
    marketing_url: str = ""
    support_url: str = ""
    privacy_policy_url: str = ""

    @property
    def has_content(self) -> bool:
        return bool(self.name or self.subtitle or self.description or self.keywords)

    @property
    def has_no_version_required_content(self) -> bool:
        return bool(self.name or self.subtitle or self.promotional_text or self.privacy_policy_url)

    @property
    def has_version_required_content(self) -> bool:
        return bool(
            self.description or self.keywords or self.whats_new or self.marketing_url or self.support_url
        )

    @property
    def is_empty(self) -> bool:
        return not self.has_no_version_required_content and not self.has_version_required_content

    @property
    def fields_over_limit(self) -> set[MetadataField]:
        over: set[MetadataField] = set()
        for f in TRANSLATABLE:
            value = getattr(self, _FIELD_ATTR[f])
            if measured_length(f, value) > FIELD_LIMITS[f].limit:
                over.add(f)
        return over

    @property
    def is_within_app_store_limits(self) -> bool:
        return not self.fields_over_limit

    def fields_cleared(self, compared_to: MetadataLocalization) -> set[MetadataField]:
        """Fields this object empties that `compared_to` (the live record) still holds."""
        cleared: set[MetadataField] = set()
        for f in TRANSLATABLE:
            attr = _FIELD_ATTR[f]
            if not getattr(self, attr) and getattr(compared_to, attr):
                cleared.add(f)
        return cleared


class LocaleState(enum.Enum):
    EMPTY = "empty"
    POPULATED = "populated"
    OPTED_IN = "optedIn"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass
class LocalizationRecord:
    id: str
    display_name: str
    existing_content: MetadataLocalization | None
    app_info_localization_id: str | None
    version_localization_id: str | None
    generated_content: MetadataLocalization
    state: LocaleState
    source_basis: MetadataLocalization | None = None
    is_approved: bool = False
    failure_message: str | None = None
    is_credential_error: bool = False


@dataclass
class ASCLocalizationData:
    locale: str
    app_info_localization_id: str | None
    version_localization_id: str | None
    name: str = ""
    subtitle: str = ""
    description: str = ""
    keywords: str = ""
    promotional_text: str = ""
    whats_new: str = ""
    marketing_url: str = ""
    support_url: str = ""
    privacy_policy_url: str = ""

    def to_metadata(self) -> MetadataLocalization:
        return MetadataLocalization(
            name=self.name,
            subtitle=self.subtitle,
            keywords=self.keywords,
            promotional_text=self.promotional_text,
            description=self.description,
            whats_new=self.whats_new,
            marketing_url=self.marketing_url,
            support_url=self.support_url,
            privacy_policy_url=self.privacy_policy_url,
        )


class PublishScope(enum.Enum):
    ALL = "all"
    NO_VERSION_REQUIRED = "noVersionRequired"
    VERSION_REQUIRED = "versionRequired"

    @property
    def limited_fields(self) -> set[MetadataField] | None:
        if self is PublishScope.ALL:
            return None
        if self is PublishScope.NO_VERSION_REQUIRED:
            return {MetadataField.NAME, MetadataField.SUBTITLE, MetadataField.PROMOTIONAL_TEXT}
        return {MetadataField.DESCRIPTION, MetadataField.KEYWORDS, MetadataField.WHATS_NEW}
