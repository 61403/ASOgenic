"""App Store Connect REST client. Source: docs/interfaces/asc-api.md.

Implements the ID resolution chain (bundle ID -> app -> appInfo -> appStoreVersion ->
localizations) and the localization read/write endpoints. No AI, no judgment — every method is a
direct, deterministic mapping onto one or more ASC calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Self, TypeVar

import httpx

from asogenic.errors import (
    ASOgenicError,
    api_message,
    app_privacy_unsupported_error,
    asc_auth_error,
    asc_state_error,
    iap_not_migrated_error,
)
from asogenic.logging_config import get_logger
from asogenic.models.metadata import ASCLocalizationData
from asogenic.services.asc_auth import ASCTokenCache
from asogenic.services.request_throttler import SyncRequestThrottler

logger = get_logger(__name__)

BASE_URL = "https://api.appstoreconnect.apple.com"

# ASC's own documented ceiling is much higher, but the write path (create/update localizations)
# is what can actually damage a live listing under a retry storm, so start conservative and let
# the AIMD ramp up on sustained success, same as the read-only Apple research clients.
STARTING_REQUESTS_PER_MINUTE = 30

EDITABLE_APP_INFO_STATES = frozenset(
    {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED", "METADATA_REJECTED"}
)
EDITABLE_VERSION_STATES = EDITABLE_APP_INFO_STATES

MAX_PAGES = 25
PAGE_LIMIT = 200
_T = TypeVar("_T")
_TRANSIENT_ASC_ERROR_CODES = frozenset(
    {"UNEXPECTED_ERROR", "INTERNAL_SERVER_ERROR", "SERVICE_UNAVAILABLE"}
)


@dataclass(frozen=True)
class BuildSummary:
    id: str
    version: str
    uses_non_exempt_encryption: bool | None
    processing_state: str | None
    expired: bool


@dataclass(frozen=True)
class ReviewDetail:
    id: str
    contact_first_name: str
    contact_last_name: str
    contact_phone: str
    contact_email: str
    demo_account_name: str
    demo_account_password: str
    demo_account_required: bool
    notes: str


@dataclass(frozen=True)
class ScreenshotSet:
    id: str
    display_type: str
    screenshot_ids: list[str]


@dataclass(frozen=True)
class PricePoint:
    id: str
    customer_price: str
    territory: str


@dataclass(frozen=True)
class BetaGroup:
    id: str
    name: str
    is_internal_group: bool
    public_link_enabled: bool


@dataclass(frozen=True)
class BetaTester:
    id: str
    email: str
    first_name: str
    last_name: str


@dataclass(frozen=True)
class BetaAppLocalization:
    id: str
    locale: str
    description: str
    feedback_email: str | None
    marketing_url: str | None
    privacy_policy_url: str | None


@dataclass(frozen=True)
class CustomerReview:
    id: str
    rating: int
    title: str
    body: str
    reviewer_nickname: str
    territory: str
    created_date: str
    response_id: str | None


@dataclass(frozen=True)
class InAppPurchase:
    id: str
    name: str
    product_id: str
    in_app_purchase_type: str
    state: str | None


@dataclass(frozen=True)
class SubscriptionGroup:
    id: str
    reference_name: str


@dataclass(frozen=True)
class Subscription:
    id: str
    name: str
    product_id: str
    subscription_period: str
    state: str | None


@dataclass(frozen=True)
class AppSummary:
    id: str
    bundle_id: str
    name: str
    platforms: list[str]
    primary_locale: str | None


@dataclass(frozen=True)
class ResolvedApp:
    app_id: str
    app_info_id: str
    version_id: str | None
    is_editable: bool
    version_state: str | None


@dataclass(frozen=True)
class LocaleIds:
    locale: str
    app_info_localization_id: str | None
    version_localization_id: str | None


class ASCClient:
    def __init__(
        self,
        token_cache: ASCTokenCache,
        *,
        transport: httpx.BaseTransport | None = None,
        throttler: SyncRequestThrottler | None = None,
    ) -> None:
        self._token_cache = token_cache
        self._http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=30.0)
        self._throttler = throttler or SyncRequestThrottler(
            starting_requests_per_minute=STARTING_REQUESTS_PER_MINUTE,
            min_requests_per_minute=10,
            max_requests_per_minute=90,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level request helper -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_cache.get_token()}"}

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._throttler.wait_for_slot()
        started = time.monotonic()
        response = self._http.request(method, path, params=params, json=json_body, headers=self._headers())
        duration_ms = int((time.monotonic() - started) * 1000)

        if response.status_code in (429, 403):
            self._throttler.report_throttled()
        else:
            self._throttler.report_success()

        logger.info(
            "asc_api %s %s -> %d (%dms, limit=%d/min)",
            method,
            path,
            response.status_code,
            duration_ms,
            self._throttler.current_requests_per_minute,
        )
        return self._handle_response(response)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(self, path: str, *, json_body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json_body=json_body)

    def _retry_transient(self, operation: Callable[[], _T]) -> _T:
        """Retry Apple's short-lived eventual-consistency failures, never validation errors."""
        for attempt in range(3):
            try:
                return operation()
            except ASOgenicError as exc:
                status = exc.detail.get("status_code")
                transient = exc.code in _TRANSIENT_ASC_ERROR_CODES or status in {500, 502, 503, 504}
                if not transient or attempt == 2:
                    raise
                time.sleep(float(attempt + 1))
        raise AssertionError("transient retry loop did not return or raise")

    def _patch(self, path: str, *, json_body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", path, json_body=json_body)

    # -- generic App Store Connect resources ---------------------------------------

    def list_resource(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """List a resource using the same bounded pagination used by the typed methods.

        New App Store Connect resources are added faster than the older typed client models.
        Keeping this small escape hatch here lets feature modules preserve auth, throttling,
        retries, and pagination without copying HTTP code or inventing a second client.
        """
        return self._get_all_pages(path, params=params or {})

    def get_resource(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._get(path, params=params)

    def create_resource(
        self,
        resource_type: str,
        *,
        attributes: dict[str, Any] | None = None,
        relationships: dict[str, Any] | None = None,
        included: list[dict[str, Any]] | None = None,
        version: str = "v1",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"type": resource_type}
        if attributes:
            data["attributes"] = attributes
        if relationships:
            data["relationships"] = relationships
        body: dict[str, Any] = {"data": data}
        if included:
            body["included"] = included
        return self._post(f"/{version}/{resource_type}", json_body=body)

    def update_resource(
        self,
        resource_type: str,
        resource_id: str,
        *,
        attributes: dict[str, Any] | None = None,
        relationships: dict[str, Any] | None = None,
        version: str = "v1",
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"type": resource_type, "id": resource_id}
        if attributes:
            data["attributes"] = attributes
        if relationships:
            data["relationships"] = relationships
        return self._patch(f"/{version}/{resource_type}/{resource_id}", json_body={"data": data})

    def delete_resource(self, resource_type: str, resource_id: str, *, version: str = "v1") -> None:
        self._request("DELETE", f"/{version}/{resource_type}/{resource_id}")

    def create_relationship(self, path: str, *, data: list[dict[str, str]] | dict[str, str]) -> dict[str, Any]:
        """Create relationship linkage data at a documented relationship endpoint."""
        return self._post(path, json_body={"data": data})

    def replace_relationship(self, path: str, *, data: list[dict[str, str]] | dict[str, str]) -> dict[str, Any]:
        """Replace relationship linkage data, preserving caller-provided ordering.

        Apple's screenshot and preview ordering endpoints use PATCH, while POST appends
        relationship members. Keep the distinction explicit so a reordering call cannot create
        duplicate linkage instead of replacing the ordered list.
        """
        return self._patch(path, json_body={"data": data})

    def delete_relationship(self, path: str, *, data: list[dict[str, str]] | dict[str, str]) -> None:
        """Delete relationship linkage data at a documented relationship endpoint."""
        self._request("DELETE", path, json_body={"data": data})

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 401:
            self._token_cache.invalidate()
            raise asc_auth_error("ASC rejected the request as unauthorized (401)")

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = None
            message = api_message(body, status_code=response.status_code, fallback=response.reason_phrase)
            raise asc_state_error(
                message,
                code=self._first_error_code(body) or f"HTTP_{response.status_code}",
                # Preserve Apple's structured field/resource diagnostics. In particular,
                # review-submission validation can return a generic entity-state title with
                # the actionable requirement only in a second error or its source pointer.
                # The MCP error serializer exposes this detail to callers instead of dropping
                # it behind the generic summary.
                detail={
                    "status_code": response.status_code,
                    "errors": self._error_details(body),
                },
            )

        if response.status_code == 204 or not response.content:
            return {}
        result: dict[str, Any] = response.json()
        return result

    @staticmethod
    def _first_error_code(body: Any) -> str | None:
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                code = errors[0].get("code")
                return str(code) if code is not None else None
        return None

    @staticmethod
    def _error_details(body: Any) -> list[dict[str, Any]]:
        errors = body.get("errors") if isinstance(body, dict) else body
        if not isinstance(errors, list):
            return []
        return [
            {
                key: value for key, value in error.items()
                if key in {"code", "title", "detail", "source", "links"}
            }
            for error in errors if isinstance(error, dict)
        ]

    def _get_all_pages(
        self, path: str, *, params: dict[str, Any], included_out: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_path: str | None = path
        next_params: dict[str, Any] | None = {**params, "limit": PAGE_LIMIT}
        for _ in range(MAX_PAGES):
            if next_path is None:
                break
            body = self._get(next_path, params=next_params)
            data = body.get("data")
            if isinstance(data, list):
                items.extend(data)
            if included_out is not None:
                included = body.get("included")
                if isinstance(included, list):
                    included_out.extend(included)
            links = body.get("links") or {}
            next_url = links.get("next")
            if not next_url:
                break
            # `links.next` is a full URL already carrying query params; strip the base URL and
            # stop passing our own params so we don't clobber the server-provided cursor.
            next_path = next_url.removeprefix(BASE_URL)
            next_params = None
        return items

    @staticmethod
    def _platform_by_version_id(included: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in included:
            if item.get("type") != "appStoreVersions":
                continue
            platform = (item.get("attributes") or {}).get("platform")
            if isinstance(platform, str):
                result[item["id"]] = platform
        return result

    @staticmethod
    def _platforms_from_relationships(item: dict[str, Any], platform_by_version_id: dict[str, str]) -> list[str]:
        """An App resource carries no `platforms` attribute — platform lives on each of its
        appStoreVersions, so this joins the app's `relationships.appStoreVersions` against the
        `included` appStoreVersions (fetched via `include=appStoreVersions`), matching the Swift
        client's `fetchApps` (reference/swift/Services/AppStoreConnectService.swift)."""
        version_refs = ((item.get("relationships") or {}).get("appStoreVersions") or {}).get("data") or []
        platforms: list[str] = []
        for ref in version_refs:
            platform = platform_by_version_id.get(ref.get("id"))
            if platform and platform not in platforms:
                platforms.append(platform)
        return platforms

    # -- ID resolution chain --------------------------------------------------------

    def find_app_by_bundle_id(self, bundle_id: str) -> AppSummary:
        body = self._get(
            "/v1/apps",
            params={
                "filter[bundleId]": bundle_id,
                "include": "appStoreVersions",
                "fields[appStoreVersions]": "platform",
            },
        )
        data = body.get("data") or []
        if not data:
            raise asc_state_error(
                f"App '{bundle_id}' does not exist in App Store Connect. Create the app record in the "
                "App Store Connect web UI, then retry.",
                code="APP_NOT_FOUND",
            )
        platform_by_version_id = self._platform_by_version_id(body.get("included") or [])
        app = data[0]
        attrs = app.get("attributes", {})
        return AppSummary(
            id=app["id"],
            bundle_id=attrs.get("bundleId", bundle_id),
            name=attrs.get("name", ""),
            platforms=self._platforms_from_relationships(app, platform_by_version_id),
            primary_locale=attrs.get("primaryLocale"),
        )

    def list_apps(self) -> list[AppSummary]:
        included: list[dict[str, Any]] = []
        items = self._get_all_pages(
            "/v1/apps",
            params={"include": "appStoreVersions", "fields[appStoreVersions]": "platform"},
            included_out=included,
        )
        platform_by_version_id = self._platform_by_version_id(included)
        result = []
        for app in items:
            attrs = app.get("attributes", {})
            result.append(
                AppSummary(
                    id=app["id"],
                    bundle_id=attrs.get("bundleId", ""),
                    name=attrs.get("name", ""),
                    platforms=self._platforms_from_relationships(app, platform_by_version_id),
                    primary_locale=attrs.get("primaryLocale"),
                )
            )
        return result

    def resolve_app_info(self, app_id: str) -> tuple[str, str | None]:
        """Returns (app_info_id, state). Prefers an editable-state appInfo, else the first."""
        body = self._get(f"/v1/apps/{app_id}/appInfos", params={"fields[appInfos]": "appStoreState"})
        data = body.get("data") or []
        if not data:
            raise asc_state_error(f"app '{app_id}' has no appInfo records", code="NOT_FOUND")

        def state_of(info: dict[str, Any]) -> str | None:
            state = info.get("attributes", {}).get("appStoreState")
            return str(state) if state is not None else None

        for info in data:
            state = state_of(info)
            if state in EDITABLE_APP_INFO_STATES:
                return info["id"], state
        first = data[0]
        return first["id"], state_of(first)

    def resolve_app_store_version(self, app_id: str, *, platform: str) -> tuple[str | None, bool, str | None]:
        """Returns (version_id, is_editable, version_state). Prefers the first version in an
        editable state (paginated up to MAX_PAGES), else the first version of any state.

        `appStoreVersions` has no `isEditable` API attribute — that was this method's original
        bug (always read a nonexistent field, so every version reported not-editable). Editability
        is derived from `appVersionState`/`appStoreState` against EDITABLE_VERSION_STATES, matching
        resolve_app_info's pattern and the Swift reference's `fetchVersionId`."""
        items = self._get_all_pages(
            f"/v1/apps/{app_id}/appStoreVersions",
            params={"filter[platform]": platform, "fields[appStoreVersions]": "appStoreState,appVersionState"},
        )
        if not items:
            return None, False, None

        def version_state(version: dict[str, Any]) -> str | None:
            attrs = version.get("attributes", {})
            state = attrs.get("appVersionState") or attrs.get("appStoreState")
            return str(state) if state is not None else None

        for version in items:
            state = version_state(version)
            if state in EDITABLE_VERSION_STATES:
                return version["id"], True, state

        first = items[0]
        return first["id"], False, version_state(first)

    def resolve_app(self, bundle_id: str, *, platform: str) -> ResolvedApp:
        app = self.find_app_by_bundle_id(bundle_id)
        if app.platforms and platform not in app.platforms:
            raise asc_state_error(
                f"app '{bundle_id}' is not available on platform '{platform}' (available: {', '.join(app.platforms)})",
                code="PLATFORM_MISMATCH",
                detail={"requested_platform": platform, "available_platforms": app.platforms},
            )
        app_info_id, _ = self.resolve_app_info(app.id)
        version_id, is_editable, version_state = self.resolve_app_store_version(app.id, platform=platform)
        return ResolvedApp(
            app_id=app.id,
            app_info_id=app_info_id,
            version_id=version_id,
            is_editable=is_editable,
            version_state=version_state,
        )

    def create_app_store_version(
        self, app_id: str, *, platform: str, version_string: str, release_type: str | None = None
    ) -> str:
        """POST /v1/appStoreVersions — the only way to get a *new* editable version once the
        current one has shipped (resolve_app_store_version only ever finds versions that already
        exist; nothing before this could create one). Apple rejects the call outright if an
        editable version already exists for this platform (`ENTITY_ERROR.RELATIONSHIP.INVALID`
        or similar, surfaced verbatim) — callers should check asogenic_resolve_app's
        `is_editable` first rather than treating this as safe to retry."""
        attributes: dict[str, Any] = {"platform": platform, "versionString": version_string}
        if release_type:
            attributes["releaseType"] = release_type
        body = {
            "data": {
                "type": "appStoreVersions",
                "attributes": attributes,
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        response = self._post("/v1/appStoreVersions", json_body=body)
        return str(response["data"]["id"])

    def count_app_store_versions(self, app_id: str, *, platform: str) -> int:
        """How many appStoreVersions this app has for the platform. >1 means the current
        editable version is an update, not a first submission — which is what makes a
        "What's New" value required on every version localization."""
        return len(
            self._get_all_pages(
                f"/v1/apps/{app_id}/appStoreVersions",
                params={"filter[platform]": platform, "fields[appStoreVersions]": "versionString"},
            )
        )

    def list_app_info_localizations(self, app_info_id: str) -> dict[str, LocaleIds]:
        items = self._get_all_pages(f"/v1/appInfos/{app_info_id}/appInfoLocalizations", params={})
        result: dict[str, LocaleIds] = {}
        for item in items:
            locale = item.get("attributes", {}).get("locale")
            if not locale:
                continue
            existing = result.get(locale)
            result[locale] = LocaleIds(
                locale=locale,
                app_info_localization_id=item["id"],
                version_localization_id=existing.version_localization_id if existing else None,
            )
        return result

    def list_app_store_version_localizations(self, version_id: str) -> dict[str, LocaleIds]:
        items = self._get_all_pages(
            f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations", params={}
        )
        result: dict[str, LocaleIds] = {}
        for item in items:
            locale = item.get("attributes", {}).get("locale")
            if not locale:
                continue
            existing = result.get(locale)
            result[locale] = LocaleIds(
                locale=locale,
                app_info_localization_id=existing.app_info_localization_id if existing else None,
                version_localization_id=item["id"],
            )
        return result

    def list_locales(self, app_info_id: str, version_id: str | None) -> dict[str, LocaleIds]:
        """Merged locale -> {app_info_localization_id, version_localization_id}."""
        merged = self.list_app_info_localizations(app_info_id)
        if version_id is None:
            return merged
        for locale, ids in self.list_app_store_version_localizations(version_id).items():
            if locale in merged:
                merged[locale] = LocaleIds(
                    locale=locale,
                    app_info_localization_id=merged[locale].app_info_localization_id,
                    version_localization_id=ids.version_localization_id,
                )
            else:
                merged[locale] = ids
        return merged

    def fetch_all_localizations(self, app_info_id: str, version_id: str | None) -> dict[str, ASCLocalizationData]:
        """Full field content per locale, merged from appInfoLocalizations (name/subtitle/privacy
        URL) and appStoreVersionLocalizations (description/keywords/promo/whats-new/marketing+
        support URL). Port of the Swift `fetchAllLocalizations` call sequence."""
        result: dict[str, ASCLocalizationData] = {}

        for item in self._get_all_pages(f"/v1/appInfos/{app_info_id}/appInfoLocalizations", params={}):
            attrs = item.get("attributes", {})
            locale = attrs.get("locale")
            if not locale:
                continue
            result[locale] = ASCLocalizationData(
                locale=locale,
                app_info_localization_id=item["id"],
                version_localization_id=None,
                name=attrs.get("name") or "",
                subtitle=attrs.get("subtitle") or "",
                privacy_policy_url=attrs.get("privacyPolicyUrl") or "",
            )

        if version_id is not None:
            for item in self._get_all_pages(
                f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations", params={}
            ):
                attrs = item.get("attributes", {})
                locale = attrs.get("locale")
                if not locale:
                    continue
                existing = result.get(locale)
                result[locale] = ASCLocalizationData(
                    locale=locale,
                    app_info_localization_id=existing.app_info_localization_id if existing else None,
                    version_localization_id=item["id"],
                    name=existing.name if existing else "",
                    subtitle=existing.subtitle if existing else "",
                    privacy_policy_url=existing.privacy_policy_url if existing else "",
                    description=attrs.get("description") or "",
                    keywords=attrs.get("keywords") or "",
                    promotional_text=attrs.get("promotionalText") or "",
                    whats_new=attrs.get("whatsNew") or "",
                    marketing_url=attrs.get("marketingUrl") or "",
                    support_url=attrs.get("supportUrl") or "",
                )
        return result

    # -- writes (publish) --------------------------------------------------------------

    def create_app_info_localization(
        self, *, app_info_id: str, locale: str, name: str = "", subtitle: str = "", privacy_policy_url: str = ""
    ) -> str:
        attributes: dict[str, Any] = {"locale": locale}
        if name:
            attributes["name"] = name
        if subtitle:
            attributes["subtitle"] = subtitle
        if privacy_policy_url:
            attributes["privacyPolicyUrl"] = privacy_policy_url
        body = {
            "data": {
                "type": "appInfoLocalizations",
                "attributes": attributes,
                "relationships": {"appInfo": {"data": {"type": "appInfos", "id": app_info_id}}},
            }
        }
        response = self._post("/v1/appInfoLocalizations", json_body=body)
        return str(response["data"]["id"])

    def update_app_info_localization(
        self, localization_id: str, *, name: str = "", subtitle: str = "", privacy_policy_url: str = ""
    ) -> None:
        """`privacy_policy_url` lives on this resource (`privacyPolicyUrl`), not on
        appStoreVersionLocalizations — it was readable via fetch_all_localizations but had no
        write path at all until this parameter was added, which meant
        asogenic_get_release_readiness's own "privacy_policy_url is not set" blocker was
        permanently unfixable through this server: nothing here could ever write it."""
        attributes: dict[str, Any] = {}
        if name:
            attributes["name"] = name
        if subtitle:
            attributes["subtitle"] = subtitle
        if privacy_policy_url:
            attributes["privacyPolicyUrl"] = privacy_policy_url
        if not attributes:
            return
        body = {"data": {"type": "appInfoLocalizations", "id": localization_id, "attributes": attributes}}
        self._patch(f"/v1/appInfoLocalizations/{localization_id}", json_body=body)

    def create_app_store_version_localization(
        self,
        *,
        version_id: str,
        locale: str,
        description: str = "",
        keywords: str = "",
        promotional_text: str = "",
        whats_new: str = "",
        support_url: str = "",
        marketing_url: str = "",
    ) -> str:
        attributes: dict[str, Any] = {"locale": locale}
        if description:
            attributes["description"] = description
        if keywords:
            attributes["keywords"] = keywords
        if promotional_text:
            attributes["promotionalText"] = promotional_text
        if whats_new:
            attributes["whatsNew"] = whats_new
        if support_url:
            attributes["supportUrl"] = support_url
        if marketing_url:
            attributes["marketingUrl"] = marketing_url
        body = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "attributes": attributes,
                "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}},
            }
        }
        response = self._post("/v1/appStoreVersionLocalizations", json_body=body)
        return str(response["data"]["id"])

    def update_app_store_version_localization(
        self,
        localization_id: str,
        *,
        description: str = "",
        keywords: str = "",
        promotional_text: str = "",
        whats_new: str = "",
        support_url: str = "",
        marketing_url: str = "",
    ) -> None:
        """`support_url`/`marketing_url` live here (appStoreVersionLocalizations), not on
        appInfoLocalizations — confirmed live against a real submission attempt: Apple's own
        reviewSubmissionItems validation rejected the version with "You must provide a value for
        the attribute 'supportUrl'" even after this server had already "published" it, because —
        same bug class as privacy_policy_url — these were readable via fetch_all_localizations
        but had no write parameter anywhere until now."""
        attributes: dict[str, Any] = {}
        if description:
            attributes["description"] = description
        if keywords:
            attributes["keywords"] = keywords
        if promotional_text:
            attributes["promotionalText"] = promotional_text
        if whats_new:
            attributes["whatsNew"] = whats_new
        if support_url:
            attributes["supportUrl"] = support_url
        if marketing_url:
            attributes["marketingUrl"] = marketing_url
        if not attributes:
            return
        body = {
            "data": {"type": "appStoreVersionLocalizations", "id": localization_id, "attributes": attributes}
        }
        self._patch(f"/v1/appStoreVersionLocalizations/{localization_id}", json_body=body)

    def get_version_copyright(self, version_id: str) -> str | None:
        body = self._get(f"/v1/appStoreVersions/{version_id}", params={"fields[appStoreVersions]": "copyright"})
        attrs = (body.get("data") or {}).get("attributes") or {}
        value = attrs.get("copyright")
        return str(value) if value else None

    def set_version_copyright(self, version_id: str, *, copyright_text: str) -> None:
        """Confirmed live: `copyright` on appStoreVersions is a hard requirement for
        reviewSubmissionItems creation ("You must provide a value for the attribute
        'copyright'"), and no tool anywhere touched it — this app's version had never had it
        set."""
        body = {"data": {"type": "appStoreVersions", "id": version_id, "attributes": {"copyright": copyright_text}}}
        self._patch(f"/v1/appStoreVersions/{version_id}", json_body=body)

    def get_content_rights_declaration(self, app_id: str) -> str | None:
        body = self._get(f"/v1/apps/{app_id}", params={"fields[apps]": "contentRightsDeclaration"})
        attrs = (body.get("data") or {}).get("attributes") or {}
        value = attrs.get("contentRightsDeclaration")
        return str(value) if value else None

    def set_content_rights_declaration(self, app_id: str, *, declaration: str) -> None:
        """Confirmed live: `contentRightsDeclaration` on `apps` is a hard requirement for
        reviewSubmissionItems creation. `declaration` is one of Apple's own enum values —
        `DOES_NOT_USE_THIRD_PARTY_CONTENT` or `USES_THIRD_PARTY_CONTENT`."""
        body = {"data": {"type": "apps", "id": app_id, "attributes": {"contentRightsDeclaration": declaration}}}
        self._patch(f"/v1/apps/{app_id}", json_body=body)

    # -- review submission (current API — appStoreVersionSubmissions is defunct for CREATE) ---

    def create_review_submission(self, app_id: str, *, platform: str) -> str:
        """The modern review-submission flow: a reviewSubmissions object first (this method),
        then one reviewSubmissionItem attaching the appStoreVersion (see
        add_review_submission_item), then confirm_review_submission to actually send it.
        Confirmed live that the older `appStoreVersionSubmissions` resource this server used to
        target is no longer creatable at all — every attempt returned
        `FORBIDDEN_ERROR: The resource 'appStoreVersionSubmissions' does not allow 'CREATE'.
        Allowed operation is: DELETE`, regardless of how ready the version was."""
        body = {
            "data": {
                "type": "reviewSubmissions",
                "attributes": {"platform": platform},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        response = self._post("/v1/reviewSubmissions", json_body=body)
        return str(response["data"]["id"])

    def get_active_review_submission(self, app_id: str, *, platform: str) -> str | None:
        """The most recent non-terminal reviewSubmission for this app+platform, if one already
        exists (Apple allows only one in flight at a time) — lets a caller resume/inspect an
        existing submission instead of blindly trying to create a duplicate."""
        items = self._get_all_pages(
            f"/v1/apps/{app_id}/reviewSubmissions",
            params={"filter[platform]": platform},
        )
        for item in items:
            state = (item.get("attributes") or {}).get("state")
            if state not in ("COMPLETE", "CANCELED"):
                return str(item["id"])
        return None

    def list_review_submission_items(self, review_submission_id: str) -> list[str]:
        items = self._get_all_pages(f"/v1/reviewSubmissions/{review_submission_id}/items", params={})
        return [str(item["id"]) for item in items]

    def add_review_submission_item(self, review_submission_id: str, *, version_id: str) -> str:
        body = {
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": review_submission_id}},
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
                },
            }
        }
        response = self._post("/v1/reviewSubmissionItems", json_body=body)
        return str(response["data"]["id"])

    def confirm_review_submission(self, review_submission_id: str) -> None:
        """Flips a reviewSubmission (already carrying its item) from READY_FOR_REVIEW into the
        actual submitted state — the `submitted: true` transition Apple's API uses in place of a
        separate "submit" action."""
        body = {
            "data": {
                "type": "reviewSubmissions",
                "id": review_submission_id,
                "attributes": {"submitted": True},
            }
        }
        self._patch(f"/v1/reviewSubmissions/{review_submission_id}", json_body=body)

    def get_review_submission_state(self, review_submission_id: str) -> str | None:
        """Raw `state` off a reviewSubmission — one of Apple's own enum values (e.g.
        `WAITING_FOR_REVIEW`, `IN_REVIEW`, `COMPLETE`, `CANCELED`). Confirmed live: this can move
        to `COMPLETE` within about a minute of confirming a submission in this environment, so a
        caller that wants to cancel should check this immediately, not assume there's a wide
        window."""
        body = self._get(
            f"/v1/reviewSubmissions/{review_submission_id}", params={"fields[reviewSubmissions]": "state"}
        )
        attrs = (body.get("data") or {}).get("attributes") or {}
        state = attrs.get("state")
        return str(state) if state is not None else None

    def cancel_review_submission(self, review_submission_id: str) -> None:
        """Withdraws a pending reviewSubmission — the `canceled: true` transition, confirmed live
        as the correct request shape (Apple accepted the shape and responded with a state-specific
        rejection, not a shape error, when tried against an already-COMPLETE submission: `409
        STATE_ERROR.ENTITY_STATE_INVALID "Resource is not in cancellable state"`). Only valid
        while the submission is still waiting/in review — once Apple finishes reviewing it
        (state becomes COMPLETE) there is nothing left to cancel; the version itself would need
        a new edit + new submission instead."""
        body = {
            "data": {
                "type": "reviewSubmissions",
                "id": review_submission_id,
                "attributes": {"canceled": True},
            }
        }
        self._patch(f"/v1/reviewSubmissions/{review_submission_id}", json_body=body)

    # -- category (app info, pre-listing setup) ---------------------------------------

    def list_categories(self) -> list[dict[str, Any]]:
        """{id, subcategories: [id, ...]} for every top-level App Store category — the fixed
        reference list `set_category` picks IDs from. Apple's own list, not this server's
        judgment: `resolve_genre` (public-listing lookup) is useless before an app ever ships,
        this is what fills that gap."""
        body = self._get("/v1/appCategories", params={"limit": 200, "include": "subcategories"})
        by_id = {item["id"]: item for item in body.get("included") or [] if item.get("type") == "appCategories"}
        result = []
        for item in body.get("data") or []:
            sub_refs = ((item.get("relationships") or {}).get("subcategories") or {}).get("data") or []
            result.append(
                {
                    "id": item["id"],
                    "subcategories": [by_id[r["id"]]["id"] for r in sub_refs if r["id"] in by_id],
                }
            )
        return result

    def get_category(self, app_info_id: str) -> tuple[str | None, str | None]:
        """Returns (primary_category_id, secondary_category_id) — either may be None if unset."""
        body = self._get(
            f"/v1/appInfos/{app_info_id}",
            params={"include": "primaryCategory,secondaryCategory", "fields[appCategories]": "platforms"},
        )
        data = body.get("data") or {}
        relationships = data.get("relationships") or {}
        primary = ((relationships.get("primaryCategory") or {}).get("data") or {}).get("id")
        secondary = ((relationships.get("secondaryCategory") or {}).get("data") or {}).get("id")
        return primary, secondary

    def set_category(
        self,
        app_info_id: str,
        *,
        primary_category_id: str,
        secondary_category_id: str | None = None,
        primary_subcategory_one_id: str | None = None,
        primary_subcategory_two_id: str | None = None,
        secondary_subcategory_one_id: str | None = None,
        secondary_subcategory_two_id: str | None = None,
    ) -> None:
        relationships: dict[str, Any] = {
            "primaryCategory": {"data": {"type": "appCategories", "id": primary_category_id}}
        }
        if secondary_category_id:
            relationships["secondaryCategory"] = {"data": {"type": "appCategories", "id": secondary_category_id}}
        for relationship, category_id in (
            ("primarySubcategoryOne", primary_subcategory_one_id),
            ("primarySubcategoryTwo", primary_subcategory_two_id),
            ("secondarySubcategoryOne", secondary_subcategory_one_id),
            ("secondarySubcategoryTwo", secondary_subcategory_two_id),
        ):
            if category_id:
                relationships[relationship] = {"data": {"type": "appCategories", "id": category_id}}
        body = {"data": {"type": "appInfos", "id": app_info_id, "relationships": relationships}}
        self._patch(f"/v1/appInfos/{app_info_id}", json_body=body)

    # -- age rating (appInfo-scoped, one declaration per appInfo) ---------------------

    def get_age_rating_declaration(self, app_info_id: str) -> dict[str, Any] | None:
        body = self._get(f"/v1/appInfos/{app_info_id}/ageRatingDeclaration", params={})
        data = body.get("data")
        return data.get("attributes") if isinstance(data, dict) else None

    def set_age_rating_declaration(self, app_info_id: str, *, attributes: dict[str, Any]) -> None:
        """`attributes` is Apple's own field set verbatim (e.g. `violenceCartoonOrFantasy:
        "NONE"|"INFREQUENT_OR_MILD"|"FREQUENT_OR_INTENSE"`, `gamblingAndContests: bool`, ...) —
        passed through rather than individually typed, since Apple has added fields to this
        resource over time and a hardcoded parameter list would silently drop new ones. The
        caller (agent + whoever actually answers these questions) owns getting the values right;
        this method's only job is writing them to the one existing declaration."""
        existing = self.get_age_rating_declaration(app_info_id)
        # ageRatingDeclaration has no direct id in the read shape above (attributes only) — refetch
        # with fields to get the id for the PATCH target, since Apple provisions this resource
        # automatically per appInfo (never created by the client, only ever updated).
        body = self._get(f"/v1/appInfos/{app_info_id}/ageRatingDeclaration", params={})
        data = body.get("data")
        if not isinstance(data, dict) or "id" not in data:
            raise asc_state_error(f"appInfo '{app_info_id}' has no ageRatingDeclaration to update", code="NOT_FOUND")
        patch_body = {"data": {"type": "ageRatingDeclarations", "id": data["id"], "attributes": attributes}}
        self._patch(f"/v1/ageRatingDeclarations/{data['id']}", json_body=patch_body)
        _ = existing  # read above only to fail fast with a clear NOT_FOUND before the real PATCH

    # -- app review information (one per appStoreVersion) ------------------------------

    def get_app_review_detail(self, version_id: str) -> ReviewDetail | None:
        body = self._get(f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail", params={})
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        attrs = data.get("attributes", {})
        return ReviewDetail(
            id=data["id"],
            contact_first_name=attrs.get("contactFirstName") or "",
            contact_last_name=attrs.get("contactLastName") or "",
            contact_phone=attrs.get("contactPhone") or "",
            contact_email=attrs.get("contactEmail") or "",
            demo_account_name=attrs.get("demoAccountName") or "",
            demo_account_password=attrs.get("demoAccountPassword") or "",
            demo_account_required=bool(attrs.get("demoAccountRequired")),
            notes=attrs.get("notes") or "",
        )

    def set_app_review_detail(
        self,
        version_id: str,
        *,
        contact_first_name: str = "",
        contact_last_name: str = "",
        contact_phone: str = "",
        contact_email: str = "",
        demo_account_name: str = "",
        demo_account_password: str = "",
        demo_account_required: bool | None = None,
        notes: str = "",
    ) -> None:
        attributes: dict[str, Any] = {}
        for key, value in (
            ("contactFirstName", contact_first_name),
            ("contactLastName", contact_last_name),
            ("contactPhone", contact_phone),
            ("contactEmail", contact_email),
            ("demoAccountName", demo_account_name),
            ("demoAccountPassword", demo_account_password),
            ("notes", notes),
        ):
            if value:
                attributes[key] = value
        if demo_account_required is not None:
            attributes["demoAccountRequired"] = demo_account_required
        if not attributes:
            return

        existing = self.get_app_review_detail(version_id)
        if existing is not None:
            # Apple treats omitted review-detail attributes as defaults on this endpoint.  A
            # notes-only PATCH can therefore unexpectedly flip demoAccountRequired or clear the
            # contact details.  Merge the current resource before patching so partial tool calls
            # have the normal PATCH semantics callers expect.
            current: dict[str, Any] = {
                "contactFirstName": existing.contact_first_name,
                "contactLastName": existing.contact_last_name,
                "contactPhone": existing.contact_phone,
                "contactEmail": existing.contact_email,
                "demoAccountName": existing.demo_account_name,
                "demoAccountPassword": existing.demo_account_password,
                "demoAccountRequired": existing.demo_account_required,
                "notes": existing.notes,
            }
            for key, value in current.items():
                attributes.setdefault(key, value)
            body = {"data": {"type": "appStoreReviewDetails", "id": existing.id, "attributes": attributes}}
            self._patch(f"/v1/appStoreReviewDetails/{existing.id}", json_body=body)
        else:
            body = {
                "data": {
                    "type": "appStoreReviewDetails",
                    "attributes": attributes,
                    "relationships": {"appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}},
                }
            }
            self._post("/v1/appStoreReviewDetails", json_body=body)

    # -- builds & export compliance -----------------------------------------------------

    def list_builds(self, app_id: str, *, platform: str | None = None) -> list[BuildSummary]:
        """`app_id` is the numeric ASC app id (resolved from the bundle id by the caller, same as
        every other app-scoped method here). Deliberately queries the top-level `/v1/builds`
        collection with `filter[app]`, not `/v1/apps/{id}/builds` — that relationship-scoped
        listing rejects `sort` and `filter[preReleaseVersion.platform]` outright (confirmed live:
        `PARAMETER_ERROR.ILLEGAL` on both params, every time), matching the same constraint the
        Swift reference client already worked around for icon lookups
        (reference/swift/Services/AppStoreConnectService.swift:fetchAppIconURL)."""
        params: dict[str, Any] = {
            "filter[app]": app_id,
            "fields[builds]": "version,usesNonExemptEncryption,processingState,expired",
            "sort": "-uploadedDate",
        }
        if platform:
            params["filter[preReleaseVersion.platform]"] = platform
        items = self._get_all_pages("/v1/builds", params=params)
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            result.append(
                BuildSummary(
                    id=item["id"],
                    version=attrs.get("version") or "",
                    uses_non_exempt_encryption=attrs.get("usesNonExemptEncryption"),
                    processing_state=attrs.get("processingState"),
                    expired=bool(attrs.get("expired")),
                )
            )
        return result

    def get_attached_build(self, version_id: str) -> str | None:
        body = self._get(f"/v1/appStoreVersions/{version_id}/relationships/build", params={})
        data = body.get("data")
        return data.get("id") if isinstance(data, dict) else None

    def attach_build(self, version_id: str, build_id: str) -> None:
        body = {
            "data": {
                "type": "appStoreVersions",
                "id": version_id,
                "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
            }
        }
        self._patch(f"/v1/appStoreVersions/{version_id}", json_body=body)

    def get_export_compliance(self, build_id: str) -> bool | None:
        body = self._get(
            f"/v1/builds/{build_id}",
            params={"fields[builds]": "usesNonExemptEncryption"},
        )
        value = ((body.get("data") or {}).get("attributes") or {}).get("usesNonExemptEncryption")
        return value if isinstance(value, bool) else None

    def set_export_compliance(self, build_id: str, *, uses_non_exempt_encryption: bool) -> None:
        """The common-case declaration: does this build use encryption beyond what's exempt
        under Apple's export-compliance rules. Apps with non-trivial custom cryptography need
        `appEncryptionDeclarations` (a fuller document, not covered here) — this covers the
        majority case (no custom crypto, or only exempt uses like HTTPS)."""
        # Apple's PATCH rejects a repeat write with the same value. Read first so retries and
        # get-then-set reconciliation are idempotent; a null/unknown value still gets written.
        current = self.get_export_compliance(build_id)
        if current is not None and current == uses_non_exempt_encryption:
            return
        body = {"data": {"type": "builds", "id": build_id, "attributes": {"usesNonExemptEncryption": uses_non_exempt_encryption}}}
        self._patch(f"/v1/builds/{build_id}", json_body=body)

    # -- screenshots ------------------------------------------------------------------

    def list_screenshot_sets(self, version_localization_id: str) -> list[ScreenshotSet]:
        body = self._get(
            f"/v1/appStoreVersionLocalizations/{version_localization_id}/appScreenshotSets",
            params={"include": "appScreenshots"},
        )
        result = []
        for item in body.get("data") or []:
            shot_refs = ((item.get("relationships") or {}).get("appScreenshots") or {}).get("data") or []
            result.append(
                ScreenshotSet(
                    id=item["id"],
                    display_type=item.get("attributes", {}).get("screenshotDisplayType") or "",
                    screenshot_ids=[r["id"] for r in shot_refs],
                )
            )
        return result

    def create_screenshot_set(self, version_localization_id: str, *, display_type: str) -> str:
        body = {
            "data": {
                "type": "appScreenshotSets",
                "attributes": {"screenshotDisplayType": display_type},
                "relationships": {
                    "appStoreVersionLocalization": {
                        "data": {"type": "appStoreVersionLocalizations", "id": version_localization_id}
                    }
                },
            }
        }
        response = self._post("/v1/appScreenshotSets", json_body=body)
        return str(response["data"]["id"])

    def reserve_screenshot(self, screenshot_set_id: str, *, file_name: str, file_size: int) -> dict[str, Any]:
        """Step 1 of Apple's 3-step asset upload: reserve a slot and get back signed upload URLs
        (`data.attributes.uploadOperations`) to PUT the raw bytes to directly — this server never
        proxies the image bytes itself, it only orchestrates the reservation/commit around a
        client-supplied upload."""
        body = {
            "data": {
                "type": "appScreenshots",
                "attributes": {"fileName": file_name, "fileSize": file_size},
                "relationships": {
                    "appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": screenshot_set_id}}
                },
            }
        }
        response = self._post("/v1/appScreenshots", json_body=body)
        data = response.get("data")
        if not isinstance(data, dict):
            raise asc_state_error("App Store Connect returned an invalid screenshot reservation")
        return data

    def get_screenshot_delivery_state(self, screenshot_id: str) -> dict[str, Any]:
        """Confirmed live: `commit_screenshot` marking an upload as uploaded is not the same as
        Apple actually accepting the asset — a real upload against a real app committed cleanly
        (no error from this server) and then permanently FAILED Apple's own asset pipeline
        (`IMAGE_ALPHA_NOT_ALLOWED`) with zero indication anywhere in this server's tool surface
        until a submission attempt discovered it, buried in a `reviewSubmissionItems`
        associatedErrors blob. `assetDeliveryState` (state + errors) is the only place that
        failure is visible, and nothing here ever read it."""
        body = self._get(
            f"/v1/appScreenshots/{screenshot_id}",
            params={"fields[appScreenshots]": "assetDeliveryState"},
        )
        attrs = (body.get("data") or {}).get("attributes") or {}
        delivery_state = attrs.get("assetDeliveryState") or {}
        return {
            "state": delivery_state.get("state"),
            "errors": delivery_state.get("errors") or [],
            "warnings": delivery_state.get("warnings") or [],
        }

    def commit_screenshot(self, screenshot_id: str, *, checksum: str) -> None:
        """Step 3: mark the reserved asset uploaded and give Apple the MD5 checksum to verify
        against what step 2 (the client's own direct PUT to the signed URL) actually sent."""
        body = {
            "data": {
                "type": "appScreenshots",
                "id": screenshot_id,
                "attributes": {"uploaded": True, "sourceFileChecksum": checksum},
            }
        }
        self._patch(f"/v1/appScreenshots/{screenshot_id}", json_body=body)

    def delete_screenshot(self, screenshot_id: str) -> None:
        self._request("DELETE", f"/v1/appScreenshots/{screenshot_id}")

    # -- pricing ------------------------------------------------------------------------

    def list_price_points(self, app_id: str, *, territory: str = "USA") -> list[PricePoint]:
        """Apple prices apps by price-point id, not raw currency amount — this lists the
        available points for one territory so a caller can pick one (e.g. the point whose
        `customer_price` is "0.0" for free) rather than needing to already know Apple's id
        scheme. `app_id` is the numeric ASC app id. Field name is `customerPrice` (+ `proceeds`)
        on the live API — the old `priceTier` attribute this used to request doesn't exist
        (`PARAMETER_ERROR.INVALID`, confirmed live), it was retired along with the old
        tier-number pricing model."""
        items = self._get_all_pages(
            f"/v1/apps/{app_id}/appPricePoints",
            params={"filter[territory]": territory, "fields[appPricePoints]": "customerPrice,proceeds"},
        )
        return [
            PricePoint(
                id=item["id"],
                customer_price=str(item.get("attributes", {}).get("customerPrice") or ""),
                territory=territory,
            )
            for item in items
        ]

    def set_base_price(self, app_id: str, *, price_point_id: str, territory: str = "USA") -> None:
        """`app_id` is the numeric ASC app id. The current pricing model requires a
        `baseTerritory` relationship on the schedule in addition to `app`/`manualPrices` —
        omitting it fails with `ENTITY_ERROR.RELATIONSHIP.REQUIRED` (confirmed live); the
        included `appPrices` record also needs its own `territory` relationship pointing at the
        same territory as the price point it references."""
        body = {
            "data": {
                "type": "appPriceSchedules",
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}},
                    "baseTerritory": {"data": {"type": "territories", "id": territory}},
                    "manualPrices": {
                        "data": [{"type": "appPrices", "id": "${new-price}"}]
                    },
                },
            },
            "included": [
                {
                    "type": "appPrices",
                    "id": "${new-price}",
                    "attributes": {"startDate": None},
                    "relationships": {
                        "appPricePoint": {"data": {"type": "appPricePoints", "id": price_point_id}},
                        "territory": {"data": {"type": "territories", "id": territory}},
                    },
                }
            ],
        }
        self._post("/v1/appPriceSchedules", json_body=body)

    def get_app_price_schedule(self, app_id: str) -> bool:
        """Whether Apple has a base price schedule for this app, including a free schedule."""
        body = self._get(f"/v1/apps/{app_id}/appPriceSchedule", params={})
        return isinstance(body.get("data"), dict)

    def get_app_availability(self, app_id: str) -> list[dict[str, Any]]:
        """Return the app's territory availability records from Apple's v2 availability API."""
        body = self._get(
            f"/v1/apps/{app_id}/appAvailabilityV2",
            params={
                "include": "territoryAvailabilities",
                "fields[territoryAvailabilities]": "available,territory",
            },
        )
        included = body.get("included") or []
        return [
            item for item in included
            if isinstance(item, dict) and item.get("type") == "territoryAvailabilities"
        ]

    # -- app privacy ("nutrition label") -------------------------------------------------
    #
    # Every method below always fails — Apple has no API-key-auth path for App Privacy at all,
    # so there is no legitimate success case or alternate failure mode to accidentally mask by
    # converting *every* ASC_STATE error here into app_privacy_unsupported_error(). Auth errors
    # (401, category asc_auth) are a different exception type and pass through unconverted.

    def list_app_data_usages(self, app_id: str) -> list[dict[str, Any]]:
        try:
            body = self._get(
                "/v1/appDataUsages",
                params={"filter[app]": app_id, "include": "category,grouping,purposes,dataProtection"},
            )
        except ASOgenicError as exc:
            raise app_privacy_unsupported_error() from exc
        return body.get("data") or []

    def add_app_data_usage(
        self,
        app_id: str,
        *,
        category_id: str,
        data_protection_id: str,
        purpose_ids: list[str],
    ) -> str:
        """One declaration = one data type (`category_id`, e.g. an Apple `appDataUsageCategories`
        id for "Contact Info > Email Address") linked to how it's protected
        (`data_protection_id`, an `appDataUsageDataProtections` id — "linked to you" vs "not
        linked" vs "used for tracking") and why it's collected (`purpose_ids`, `appDataUsagePurposes`
        ids). All three id sets are Apple's fixed reference values (documented, not discoverable
        via a list endpoint) — this method is intentionally mechanical: it writes exactly the
        declaration it's given, the accuracy of that declaration is not this server's call to
        make."""
        body = {
            "data": {
                "type": "appDataUsages",
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}},
                    "category": {"data": {"type": "appDataUsageCategories", "id": category_id}},
                    "dataProtection": {"data": {"type": "appDataUsageDataProtections", "id": data_protection_id}},
                    "purposes": {"data": [{"type": "appDataUsagePurposes", "id": p} for p in purpose_ids]},
                },
            }
        }
        try:
            response = self._post("/v1/appDataUsages", json_body=body)
        except ASOgenicError as exc:
            raise app_privacy_unsupported_error() from exc
        return str(response["data"]["id"])

    def delete_app_data_usage(self, usage_id: str) -> None:
        try:
            self._request("DELETE", f"/v1/appDataUsages/{usage_id}")
        except ASOgenicError as exc:
            raise app_privacy_unsupported_error() from exc

    def set_app_privacy_publish_state(self, app_id: str, *, published: bool) -> None:
        """Declaring individual data-usage entries doesn't make them visible on the listing —
        the app's `appDataUsagePublishState` relationship has to be flipped to "published"
        separately once every entry is correct."""
        body = {
            "data": {
                "type": "apps",
                "id": app_id,
                "attributes": {"appDataUsagePublishState": "published" if published else "notPublished"},
            }
        }
        try:
            self._patch(f"/v1/apps/{app_id}", json_body=body)
        except ASOgenicError as exc:
            raise app_privacy_unsupported_error() from exc

    # -- TestFlight (beta groups, testers, build localizations, beta review) ------------
    #
    # Implemented against Apple's documented ASC OpenAPI schema, the same way every method above
    # was originally written — but NOT yet "confirmed live" against a real account the way the
    # release-pipeline methods above have been (see their docstrings' "confirmed live" notes).
    # If a live call here surfaces a shape mismatch, that's new information the same way the
    # copyright/contentRightsDeclaration/supportUrl gaps were originally found — fix the method,
    # don't route around it in the tool layer.

    def list_beta_groups(self, app_id: str) -> list[BetaGroup]:
        items = self._get_all_pages(
            f"/v1/apps/{app_id}/betaGroups",
            params={"fields[betaGroups]": "name,isInternalGroup,publicLinkEnabled"},
        )
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            result.append(
                BetaGroup(
                    id=item["id"],
                    name=attrs.get("name") or "",
                    is_internal_group=bool(attrs.get("isInternalGroup")),
                    public_link_enabled=bool(attrs.get("publicLinkEnabled")),
                )
            )
        return result

    def create_beta_group(self, app_id: str, *, name: str) -> str:
        body = {
            "data": {
                "type": "betaGroups",
                "attributes": {"name": name},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        response = self._post("/v1/betaGroups", json_body=body)
        return str(response["data"]["id"])

    def delete_beta_group(self, group_id: str) -> None:
        self._request("DELETE", f"/v1/betaGroups/{group_id}")

    def list_beta_testers(self, *, app_id: str | None = None, group_id: str | None = None) -> list[BetaTester]:
        """Exactly one of `app_id` (every tester on the app, across all groups) or `group_id`
        (testers in one group) must be given — the caller (tools/testflight.py) enforces that.

        Confirmed live: `/v1/apps/{id}/betaTesters` 403s (`FORBIDDEN_ERROR`, "relationship
        'betaTesters' does not allow 'GET_RELATED'. Allowed operation is: DELETE") — that nested
        route only supports removing a tester from the app, not listing. The working equivalent
        is the top-level betaTesters collection filtered by app."""
        if group_id is not None:
            path = f"/v1/betaGroups/{group_id}/betaTesters"
            params = {"fields[betaTesters]": "email,firstName,lastName"}
        else:
            path = "/v1/betaTesters"
            params = {"fields[betaTesters]": "email,firstName,lastName"}
            if app_id is not None:
                params["filter[apps]"] = app_id
        items = self._get_all_pages(path, params=params)
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            result.append(
                BetaTester(
                    id=item["id"],
                    email=attrs.get("email") or "",
                    first_name=attrs.get("firstName") or "",
                    last_name=attrs.get("lastName") or "",
                )
            )
        return result

    def invite_beta_tester(self, group_id: str, *, email: str, first_name: str = "", last_name: str = "") -> str:
        """Creates the tester and adds them to `group_id` in one call — the invite email Apple
        sends is a side effect of this relationship, not a separate step."""
        attributes: dict[str, Any] = {"email": email}
        if first_name:
            attributes["firstName"] = first_name
        if last_name:
            attributes["lastName"] = last_name
        body = {
            "data": {
                "type": "betaTesters",
                "attributes": attributes,
                "relationships": {"betaGroups": {"data": [{"type": "betaGroups", "id": group_id}]}},
            }
        }
        response = self._post("/v1/betaTesters", json_body=body)
        return str(response["data"]["id"])

    def list_beta_app_localizations(self, app_id: str) -> list[BetaAppLocalization]:
        items = self._get_all_pages(
            f"/v1/apps/{app_id}/betaAppLocalizations",
            params={
                "fields[betaAppLocalizations]":
                "locale,description,feedbackEmail,marketingUrl,privacyPolicyUrl"
            },
        )
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            result.append(
                BetaAppLocalization(
                    id=item["id"],
                    locale=attrs.get("locale") or "",
                    description=attrs.get("description") or "",
                    feedback_email=attrs.get("feedbackEmail"),
                    marketing_url=attrs.get("marketingUrl"),
                    privacy_policy_url=attrs.get("privacyPolicyUrl"),
                )
            )
        return result

    def set_beta_app_localization(
        self,
        app_id: str,
        *,
        locale: str,
        description: str,
        feedback_email: str | None = None,
        marketing_url: str | None = None,
        privacy_policy_url: str | None = None,
    ) -> str:
        existing = next((item for item in self.list_beta_app_localizations(app_id) if item.locale == locale), None)
        attributes: dict[str, Any] = {"description": description}
        for key, value in (
            ("feedbackEmail", feedback_email),
            ("marketingUrl", marketing_url),
            ("privacyPolicyUrl", privacy_policy_url),
        ):
            if value is not None:
                attributes[key] = value
        if existing is not None:
            body = {"data": {"type": "betaAppLocalizations", "id": existing.id, "attributes": attributes}}
            self._patch(f"/v1/betaAppLocalizations/{existing.id}", json_body=body)
            return existing.id
        body = {
            "data": {
                "type": "betaAppLocalizations",
                "attributes": {"locale": locale, **attributes},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        response = self._post("/v1/betaAppLocalizations", json_body=body)
        return str(response["data"]["id"])

    def remove_beta_tester_from_group(self, group_id: str, *, tester_id: str) -> None:
        body = {"data": [{"type": "betaTesters", "id": tester_id}]}
        self._request("DELETE", f"/v1/betaGroups/{group_id}/relationships/betaTesters", json_body=body)

    def delete_beta_tester(self, tester_id: str) -> None:
        self._request("DELETE", f"/v1/betaTesters/{tester_id}")

    def get_beta_build_localization_id(self, build_id: str, *, locale: str) -> str | None:
        items = self._get_all_pages(
            f"/v1/builds/{build_id}/betaBuildLocalizations", params={"fields[betaBuildLocalizations]": "locale"}
        )
        for item in items:
            if item.get("attributes", {}).get("locale") == locale:
                return str(item["id"])
        return None

    def set_beta_build_notes(self, build_id: str, *, locale: str, whats_new: str) -> None:
        """Create-or-update, same shape as set_app_review_detail — Apple provisions no default
        betaBuildLocalization per build/locale, so a first call for a given locale POSTs and every
        call after that PATCHes."""
        existing_id = self.get_beta_build_localization_id(build_id, locale=locale)
        if existing_id is not None:
            body = {
                "data": {"type": "betaBuildLocalizations", "id": existing_id, "attributes": {"whatsNew": whats_new}}
            }
            self._patch(f"/v1/betaBuildLocalizations/{existing_id}", json_body=body)
        else:
            body = {
                "data": {
                    "type": "betaBuildLocalizations",
                    "attributes": {"locale": locale, "whatsNew": whats_new},
                    "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
                }
            }
            self._post("/v1/betaBuildLocalizations", json_body=body)

    def submit_beta_review(self, build_id: str) -> str:
        body = {
            "data": {
                "type": "betaAppReviewSubmissions",
                "relationships": {"build": {"data": {"type": "builds", "id": build_id}}},
            }
        }
        response = self._post("/v1/betaAppReviewSubmissions", json_body=body)
        return str(response["data"]["id"])

    def get_beta_review_state(self, build_id: str) -> str | None:
        items = self._get_all_pages(
            "/v1/betaAppReviewSubmissions",
            params={"filter[build]": build_id, "fields[betaAppReviewSubmissions]": "betaReviewState"},
        )
        if not items:
            return None
        state = items[0].get("attributes", {}).get("betaReviewState")
        return str(state) if state is not None else None

    # -- Customer reviews & responses ---------------------------------------------------

    def list_customer_reviews(
        self, app_id: str, *, territory: str | None = None
    ) -> list[CustomerReview]:
        params: dict[str, Any] = {
            "include": "response",
            "fields[customerReviews]": "rating,title,body,reviewerNickname,territory,createdDate,response",
        }
        if territory:
            params["filter[territory]"] = territory
        included: list[dict[str, Any]] = []
        items = self._get_all_pages(f"/v1/apps/{app_id}/customerReviews", params=params, included_out=included)
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            response_ref = ((item.get("relationships") or {}).get("response") or {}).get("data")
            response_id = response_ref.get("id") if isinstance(response_ref, dict) else None
            result.append(
                CustomerReview(
                    id=item["id"],
                    rating=int(attrs.get("rating") or 0),
                    title=attrs.get("title") or "",
                    body=attrs.get("body") or "",
                    reviewer_nickname=attrs.get("reviewerNickname") or "",
                    territory=attrs.get("territory") or "",
                    created_date=attrs.get("createdDate") or "",
                    response_id=response_id,
                )
            )
        return result

    def reply_to_review(self, review_id: str, *, response_body: str) -> str:
        """Apple's API has no PATCH for customerReviewResponses — a review with an existing
        response must go through delete_review_response first (confirmed by Apple's own published
        API reference: CREATE and DELETE are the only operations this resource allows)."""
        body = {
            "data": {
                "type": "customerReviewResponses",
                "attributes": {"responseBody": response_body},
                "relationships": {"review": {"data": {"type": "customerReviews", "id": review_id}}},
            }
        }
        response = self._post("/v1/customerReviewResponses", json_body=body)
        return str(response["data"]["id"])

    def delete_review_response(self, response_id: str) -> None:
        self._request("DELETE", f"/v1/customerReviewResponses/{response_id}")

    # -- In-app purchases & subscriptions ------------------------------------------------

    # Apple codes that mean "this type/path doesn't exist for me" rather than "your input was
    # invalid" — the signature of an app that hasn't opted into API-based IAP management yet.
    # Once migrated, a real validation failure (duplicate product id, bad type, etc.) comes back
    # as an ENTITY_ERROR.* code instead, which this set deliberately excludes so a genuine error
    # after migration is never misreported as "go migrate this app".
    _IAP_MIGRATION_ERROR_CODES: ClassVar[set[str]] = {"PARAMETER_ERROR.INVALID", "NOT_FOUND", "PATH_ERROR"}

    def _reraise_if_iap_not_migrated(self, exc: ASOgenicError, *, subscription: bool = False) -> None:
        message = exc.message.lower()
        resource_is_iap = "inapppurchase" in message or (subscription and "subscription" in message)
        if exc.code in self._IAP_MIGRATION_ERROR_CODES and resource_is_iap:
            raise iap_not_migrated_error() from exc
        raise exc

    def list_in_app_purchases(self, app_id: str) -> list[InAppPurchase]:
        """Confirmed live: the collection path is `inAppPurchasesV2`, but each returned resource's
        JSON:API `type` is `inAppPurchases` (matching create_in_app_purchase's own write-side
        type) — Apple's sparse-fieldset param must reference that resource type, not the path
        segment. `fields[inAppPurchasesV2]` 400s with `PARAMETER_ERROR.INVALID
        "'inAppPurchasesV2' is not a valid type name"`, which used to get misclassified by
        _reraise_if_iap_not_migrated as "this app was never migrated" (its message happened to
        contain "inapppurchase") even on an app confirmed live to already have working IAP
        management — telling a caller to go make an irreversible ASC settings change that was
        never the actual problem."""
        try:
            items = self._get_all_pages(
                f"/v1/apps/{app_id}/inAppPurchasesV2",
                params={"fields[inAppPurchases]": "name,productId,inAppPurchaseType,state"},
            )
        except ASOgenicError as exc:
            self._reraise_if_iap_not_migrated(exc)
        result = []
        for item in items:
            attrs = item.get("attributes", {})
            result.append(
                InAppPurchase(
                    id=item["id"],
                    name=attrs.get("name") or "",
                    product_id=attrs.get("productId") or "",
                    in_app_purchase_type=attrs.get("inAppPurchaseType") or "",
                    state=attrs.get("state"),
                )
            )
        return result

    def create_in_app_purchase(
        self, app_id: str, *, name: str, product_id: str, in_app_purchase_type: str, review_note: str = ""
    ) -> str:
        """Confirmed live: POSTing to `/v1/inAppPurchasesV2` with type `inAppPurchasesV2` 404s
        (`NOT_FOUND`, "the path provided does not match a defined resource type"). Apple's
        `inAppPurchasesV2` type/path only exists for the read-side relationship under
        `/v1/apps/{id}/inAppPurchasesV2` — every write (create/update/delete) goes through the
        `/v2/inAppPurchases` collection with resource type `inAppPurchases` instead. Even that
        collection still needs the account-level IAP-API migration — see
        _reraise_if_iap_not_migrated."""
        attributes: dict[str, Any] = {
            "name": name,
            "productId": product_id,
            "inAppPurchaseType": in_app_purchase_type,
        }
        if review_note:
            attributes["reviewNote"] = review_note
        body = {
            "data": {
                "type": "inAppPurchases",
                "attributes": attributes,
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        try:
            response = self._post("/v2/inAppPurchases", json_body=body)
        except ASOgenicError as exc:
            self._reraise_if_iap_not_migrated(exc)
        return str(response["data"]["id"])

    def delete_in_app_purchase(self, iap_id: str) -> None:
        """Write-side collection is `/v2/inAppPurchases` (same as create), not the read-only
        `/v1/apps/{id}/inAppPurchasesV2` relationship. Apple only permits deleting an IAP that
        has never been approved — one already `APPROVED` returns `STATE_ERROR` and can only be
        removed from sale via the ASC web UI. Same account-migration prerequisite as the rest of
        this section — see _reraise_if_iap_not_migrated."""
        try:
            self._request("DELETE", f"/v2/inAppPurchases/{iap_id}")
        except ASOgenicError as exc:
            self._reraise_if_iap_not_migrated(exc)

    def set_in_app_purchase_price(self, iap_id: str, *, price_point_id: str, territory: str = "USA") -> None:
        """Same appPriceSchedules-style shape as set_base_price: a schedule resource carrying a
        `manualPrices` relationship to an included price record referencing the chosen price
        point, plus a `baseTerritory` relationship."""
        body = {
            "data": {
                "type": "inAppPurchasePriceSchedules",
                "relationships": {
                    # The schedule resource's relationship is named inAppPurchase.  The v2
                    # collection path and price object's inAppPurchaseV2 relationship are
                    # separate API concepts; using the latter here makes Apple reject a
                    # newly-created IAP's first price assignment.
                    "inAppPurchase": {"data": {"type": "inAppPurchases", "id": iap_id}},
                    "baseTerritory": {"data": {"type": "territories", "id": territory}},
                    "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": "${new-price}"}]},
                },
            },
            "included": [
                {
                    "type": "inAppPurchasePrices",
                    "id": "${new-price}",
                    "attributes": {"startDate": None},
                    "relationships": {
                        "inAppPurchasePricePoint": {"data": {"type": "inAppPurchasePricePoints", "id": price_point_id}},
                        "territory": {"data": {"type": "territories", "id": territory}},
                    },
                }
            ],
        }
        self._post("/v1/inAppPurchasePriceSchedules", json_body=body)

    def list_subscription_groups(self, app_id: str) -> list[SubscriptionGroup]:
        try:
            items = self._get_all_pages(
                f"/v1/apps/{app_id}/subscriptionGroups", params={"fields[subscriptionGroups]": "referenceName"}
            )
        except ASOgenicError as exc:
            self._reraise_if_iap_not_migrated(exc, subscription=True)
        return [
            SubscriptionGroup(id=item["id"], reference_name=item.get("attributes", {}).get("referenceName") or "")
            for item in items
        ]

    def list_subscriptions(self, group_id: str) -> list[Subscription]:
        """List subscription products in a group, including product IDs.

        Apple exposes this relationship directly; callers should not have to infer product IDs
        from duplicate-create errors or copy them manually from App Store Connect.
        """
        try:
            items = self._get_all_pages(
                f"/v1/subscriptionGroups/{group_id}/subscriptions",
                params={"fields[subscriptions]": "name,productId,subscriptionPeriod,state"},
            )
        except ASOgenicError as exc:
            self._reraise_if_iap_not_migrated(exc, subscription=True)
        return [
            Subscription(
                id=item["id"], name=(item.get("attributes") or {}).get("name") or "",
                product_id=(item.get("attributes") or {}).get("productId") or "",
                subscription_period=(item.get("attributes") or {}).get("subscriptionPeriod") or "",
                state=(item.get("attributes") or {}).get("state"),
            )
            for item in items
        ]

    def create_subscription_group(self, app_id: str, *, reference_name: str) -> str:
        body = {
            "data": {
                "type": "subscriptionGroups",
                "attributes": {"referenceName": reference_name},
                "relationships": {"app": {"data": {"type": "apps", "id": app_id}}},
            }
        }
        response = self._post("/v1/subscriptionGroups", json_body=body)
        return str(response["data"]["id"])

    def delete_subscription_group(self, group_id: str) -> None:
        """Apple rejects deleting a group that still contains any subscription
        (`STATE_ERROR`/`ENTITY_ERROR`) — delete the individual subscriptions first."""
        self._retry_transient(lambda: self._request("DELETE", f"/v1/subscriptionGroups/{group_id}"))

    def create_subscription(
        self, group_id: str, *, name: str, product_id: str, subscription_period: str, review_note: str = ""
    ) -> str:
        attributes: dict[str, Any] = {
            "name": name,
            "productId": product_id,
            "subscriptionPeriod": subscription_period,
        }
        if review_note:
            attributes["reviewNote"] = review_note
        body = {
            "data": {
                "type": "subscriptions",
                "attributes": attributes,
                "relationships": {"group": {"data": {"type": "subscriptionGroups", "id": group_id}}},
            }
        }
        response = self._post("/v1/subscriptions", json_body=body)
        return str(response["data"]["id"])

    def delete_subscription(self, subscription_id: str) -> None:
        """Apple only permits deleting a subscription that has never been approved — one already
        `APPROVED` returns `STATE_ERROR` and can only be removed from sale via the ASC web UI."""
        self._request("DELETE", f"/v1/subscriptions/{subscription_id}")

    def list_subscription_price_points(self, subscription_id: str, *, territory: str = "USA") -> list[PricePoint]:
        """Subscription price points are a separate Apple resource collection from
        `appPricePoints` — confirmed live: feeding an `appPricePoints` id into
        set_subscription_price fails with `ENTITY_ERROR.RELATIONSHIP.INVALID` ("Invalid
        subscriptionPricePoint id ..."). This lists the ids actually valid for
        set_subscription_price, same pagination/filtering shape as list_price_points."""
        items = self._get_all_pages(
            f"/v1/subscriptions/{subscription_id}/pricePoints",
            params={"filter[territory]": territory, "fields[subscriptionPricePoints]": "customerPrice,proceeds"},
        )
        return [
            PricePoint(
                id=item["id"],
                customer_price=str(item.get("attributes", {}).get("customerPrice") or ""),
                territory=territory,
            )
            for item in items
        ]

    def _subscription_has_any_price(self, subscription_id: str) -> bool:
        response = self._get(f"/v1/subscriptions/{subscription_id}/relationships/prices")
        return bool(response.get("data"))

    def set_subscription_price(self, subscription_id: str, *, price_point_id: str, territory: str = "USA") -> None:
        """Confirmed live: `POST /v1/subscriptionPrices` only works for *changing* the price of a
        subscription that already has one — on a brand-new subscription with no price yet, Apple
        rejects it with `ENTITY_ERROR.RELATIONSHIP.INVALID` even though every relationship id is
        valid, because that endpoint has nothing to "change" (matches the documented behavior of
        real ASC clients, e.g. rorkai/App-Store-Connect-CLI's SetSubscriptionInitialPrice). The
        first price for a subscription must instead be set via `PATCH /v1/subscriptions/{id}`
        with an inline `included` price resource, the same local-id/`included` pattern already
        used by set_base_price above."""
        if self._subscription_has_any_price(subscription_id):
            body = {
                "data": {
                    "type": "subscriptionPrices",
                    "relationships": {
                        "subscription": {"data": {"type": "subscriptions", "id": subscription_id}},
                        "subscriptionPricePoint": {"data": {"type": "subscriptionPricePoints", "id": price_point_id}},
                        "territory": {"data": {"type": "territories", "id": territory}},
                    },
                }
            }
            self._post("/v1/subscriptionPrices", json_body=body)
        else:
            initial_price_body: dict[str, Any] = {
                "data": {
                    "type": "subscriptions",
                    "id": subscription_id,
                    "relationships": {
                        "prices": {"data": [{"type": "subscriptionPrices", "id": "${new-price}"}]},
                    },
                },
                "included": [
                    {
                        "type": "subscriptionPrices",
                        "id": "${new-price}",
                        "relationships": {
                            "subscriptionPricePoint": {
                                "data": {"type": "subscriptionPricePoints", "id": price_point_id}
                            },
                            "territory": {"data": {"type": "territories", "id": territory}},
                        },
                    }
                ],
            }
            self._patch(f"/v1/subscriptions/{subscription_id}", json_body=initial_price_body)

    def get_subscription_localization_id(self, subscription_id: str, *, locale: str) -> str | None:
        items = self._get_all_pages(
            f"/v1/subscriptions/{subscription_id}/subscriptionLocalizations",
            params={"fields[subscriptionLocalizations]": "locale"},
        )
        for item in items:
            if item.get("attributes", {}).get("locale") == locale:
                return str(item["id"])
        return None

    def set_subscription_localization(
        self, subscription_id: str, *, locale: str, name: str, description: str = ""
    ) -> None:
        """Create-or-update, same shape as set_beta_build_notes/set_app_review_detail above. This
        is the ASO-relevant piece of the subscription surface: display name/description are what
        actually shows on the paywall."""
        attributes: dict[str, Any] = {"name": name}
        if description:
            attributes["description"] = description
        def write() -> None:
            existing_id = self.get_subscription_localization_id(subscription_id, locale=locale)
            if existing_id is not None:
                body = {"data": {"type": "subscriptionLocalizations", "id": existing_id, "attributes": attributes}}
                self._patch(f"/v1/subscriptionLocalizations/{existing_id}", json_body=body)
                return
            create_attributes = {**attributes, "locale": locale}
            body = {
                "data": {
                    "type": "subscriptionLocalizations",
                    "attributes": create_attributes,
                    "relationships": {"subscription": {"data": {"type": "subscriptions", "id": subscription_id}}},
                }
            }
            self._post("/v1/subscriptionLocalizations", json_body=body)

        self._retry_transient(write)
