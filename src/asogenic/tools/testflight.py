"""TestFlight: beta groups, testers, per-build "what to test" notes, and external beta review
submission. Post-launch/ops tools, not part of the asogenic_submit_for_review pipeline — a build
can go through TestFlight independently of, and before, an App Store review submission.

Implemented against Apple's documented ASC OpenAPI schema, the same way every existing ASC-facing
tool was originally written — not yet "confirmed live" against a real account the way the
release-pipeline tools are (see release_setup.py/readiness.py docstrings' "confirmed live" notes).
If a live call here surfaces a shape mismatch, fix the ASCClient method, don't route around it.
"""

from __future__ import annotations

from typing import Any

from asogenic import context
from asogenic.errors import validation_error


def list_beta_groups(*, app_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    groups = client.list_beta_groups(numeric_app_id)
    return {
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "is_internal": g.is_internal_group,
                "public_link_enabled": g.public_link_enabled,
            }
            for g in groups
        ]
    }


def create_beta_group(*, app_id: str, name: str) -> dict[str, Any]:
    if not name.strip():
        raise validation_error("name must not be empty", field_name="name")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    group_id = client.create_beta_group(numeric_app_id, name=name.strip())
    return {"id": group_id}


def delete_beta_group(*, group_id: str) -> dict[str, Any]:
    """Delete an external beta group outright — testers lose access to builds shared only through
    it (a tester in other groups keeps those). Internal groups can't be deleted this way."""
    if not group_id.strip():
        raise validation_error("group_id must not be empty", field_name="group_id")
    client = context.get_asc_client()
    client.delete_beta_group(group_id.strip())
    return {"deleted": True}


def list_beta_testers(*, app_id: str, group_id: str | None = None) -> dict[str, Any]:
    """Every tester on the app if `group_id` is omitted, else just that group's testers."""
    client = context.get_asc_client()
    if group_id is not None:
        testers = client.list_beta_testers(group_id=group_id)
    else:
        numeric_app_id = client.find_app_by_bundle_id(app_id).id
        testers = client.list_beta_testers(app_id=numeric_app_id)
    return {
        "testers": [
            {"id": t.id, "email": t.email, "first_name": t.first_name, "last_name": t.last_name} for t in testers
        ]
    }


def invite_beta_tester(*, group_id: str, email: str, first_name: str = "", last_name: str = "") -> dict[str, Any]:
    """Creates the tester and adds them to `group_id` in one call — Apple's invite email is a
    side effect of the betaGroups relationship on the create, not a separate action."""
    if not email.strip():
        raise validation_error("email must not be empty", field_name="email")
    client = context.get_asc_client()
    tester_id = client.invite_beta_tester(group_id, email=email.strip(), first_name=first_name, last_name=last_name)
    return {"id": tester_id, "invited": True}


def remove_beta_tester(*, tester_id: str, group_id: str | None = None) -> dict[str, Any]:
    """Removes the tester from `group_id` if given, else deletes the tester outright (removing
    them from every group and revoking their TestFlight access to this app)."""
    client = context.get_asc_client()
    if group_id is not None:
        client.remove_beta_tester_from_group(group_id, tester_id=tester_id)
    else:
        client.delete_beta_tester(tester_id)
    return {"removed": True}


def set_beta_build_notes(*, build_id: str, locale: str, whats_new: str) -> dict[str, Any]:
    """The beta-facing "what to test" notes for one build+locale — separate from the App Store
    version's whatsNew field (asogenic_publish), since a build can be in TestFlight well before
    any of its text metadata is ready for the App Store listing."""
    if not locale.strip():
        raise validation_error("locale must not be empty", field_name="locale")
    client = context.get_asc_client()
    client.set_beta_build_notes(build_id, locale=locale, whats_new=whats_new)
    return {"set": True}


def list_beta_app_localizations(*, app_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    localizations = client.list_beta_app_localizations(numeric_app_id)
    return {
        "localizations": [
            {
                "id": item.id,
                "locale": item.locale,
                "description": item.description,
                "feedback_email": item.feedback_email,
                "marketing_url": item.marketing_url,
                "privacy_policy_url": item.privacy_policy_url,
            }
            for item in localizations
        ]
    }


def set_beta_app_localization(
    *,
    app_id: str,
    locale: str,
    description: str,
    feedback_email: str | None = None,
    marketing_url: str | None = None,
    privacy_policy_url: str | None = None,
) -> dict[str, Any]:
    if not locale.strip():
        raise validation_error("locale must not be empty", field_name="locale")
    if not description.strip():
        raise validation_error("description must not be empty", field_name="description")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    localization_id = client.set_beta_app_localization(
        numeric_app_id,
        locale=locale.strip(),
        description=description.strip(),
        feedback_email=feedback_email,
        marketing_url=marketing_url,
        privacy_policy_url=privacy_policy_url,
    )
    return {"set": True, "id": localization_id, "locale": locale.strip()}


def submit_beta_review(*, build_id: str) -> dict[str, Any]:
    """Submits a build for Apple's external-beta review — required before testers in a
    non-internal group can install it (internal-group testers never need this: internal groups
    skip beta review entirely)."""
    client = context.get_asc_client()
    submission_id = client.submit_beta_review(build_id)
    return {"submitted": True, "submission_id": submission_id}


def get_beta_review_status(*, build_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    state = client.get_beta_review_state(build_id)
    return {"state": state}
