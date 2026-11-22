"""JSON-safe (de)serialization for WorkspaceSession, mirroring pool_serde.py's pattern — needed
so a tenant's session state can round-trip through SQLite (session.py's SessionStore) instead of
being lost on every process restart."""

from __future__ import annotations

from typing import Any

from asogenic.models.metadata import LocaleState, LocalizationRecord, MetadataLocalization
from asogenic.session import WorkspaceSession


def _metadata_to_dict(m: MetadataLocalization | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {
        "name": m.name,
        "subtitle": m.subtitle,
        "keywords": m.keywords,
        "promotional_text": m.promotional_text,
        "description": m.description,
        "whats_new": m.whats_new,
        "marketing_url": m.marketing_url,
        "support_url": m.support_url,
        "privacy_policy_url": m.privacy_policy_url,
    }


def _metadata_from_dict(data: dict[str, Any] | None) -> MetadataLocalization | None:
    if data is None:
        return None
    return MetadataLocalization(**data)


def _record_to_dict(record: LocalizationRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "display_name": record.display_name,
        "existing_content": _metadata_to_dict(record.existing_content),
        "app_info_localization_id": record.app_info_localization_id,
        "version_localization_id": record.version_localization_id,
        "generated_content": _metadata_to_dict(record.generated_content),
        "state": record.state.value,
        "source_basis": _metadata_to_dict(record.source_basis),
        "is_approved": record.is_approved,
        "failure_message": record.failure_message,
        "is_credential_error": record.is_credential_error,
    }


def _record_from_dict(data: dict[str, Any]) -> LocalizationRecord:
    generated = _metadata_from_dict(data["generated_content"])
    return LocalizationRecord(
        id=data["id"],
        display_name=data["display_name"],
        existing_content=_metadata_from_dict(data["existing_content"]),
        app_info_localization_id=data["app_info_localization_id"],
        version_localization_id=data["version_localization_id"],
        generated_content=generated if generated is not None else MetadataLocalization(),
        state=LocaleState(data["state"]),
        source_basis=_metadata_from_dict(data.get("source_basis")),
        is_approved=data.get("is_approved", False),
        failure_message=data.get("failure_message"),
        is_credential_error=data.get("is_credential_error", False),
    )


def session_to_dict(session: WorkspaceSession) -> dict[str, Any]:
    return {
        "records": {locale_id: _record_to_dict(r) for locale_id, r in session.records.items()},
        "published_content_hash": session.published_hashes(),
        "product_context": session.product_context,
    }


def session_from_dict(data: dict[str, Any]) -> WorkspaceSession:
    session = WorkspaceSession()
    for locale_id, record_data in data.get("records", {}).items():
        session.records[locale_id] = _record_from_dict(record_data)
    session.load_published_hashes(data.get("published_content_hash", {}))
    session.product_context = data.get("product_context")
    return session
