"""In-app purchases and subscriptions: create them, price them, and — the ASO-relevant piece —
set the subscription display name/description that actually shows on the paywall. Post-launch/ops
tools, not part of the release pipeline.

OUT OF SCOPE, deliberately, same honest treatment as App Privacy in release_setup.py: submitting
an IAP/subscription for review requires an uploaded review screenshot, a binary-asset upload this
server has no path for outside the existing asogenic_upload_screenshot flow (which targets app
store screenshots, a different resource). Wiring that in is its own follow-up, not attempted here
— every tool below creates/configures records in DRAFT/PREPARE_FOR_SUBMISSION state, but nothing
here submits one for Apple's review.

Implemented against Apple's documented ASC OpenAPI schema — see tools/testflight.py's module
docstring for the same "not yet confirmed live" caveat that applies here too.

CONFIRMED LIVE, one real gap: every `inAppPurchasesV2`/`subscriptions` call here 404s/400s with a
generic-sounding type-not-found error for an app that has never been migrated to API-based IAP
management. Apple requires a one-time, irreversible "Manage In-App Purchases and Subscriptions
via the App Store Connect API" opt-in per app (App Store Connect web UI, Features tab) before
these endpoints work at all — this is an Apple-side account/app prerequisite, not a bug in the
endpoints themselves, which match Apple's current documented schema.
"""

from __future__ import annotations

from typing import Any

from asogenic import context
from asogenic.errors import ASOgenicError, validation_error

_IAP_TYPES = {"CONSUMABLE", "NON_CONSUMABLE", "NON_RENEWING_SUBSCRIPTION"}
# Public API takes ISO 8601-style periods; Apple's own subscriptionPeriod enum uses spelled-out
# names (confirmed live: sending 'P1M' verbatim gets ENTITY_ERROR.ATTRIBUTE.TYPE from ASC).
_SUBSCRIPTION_PERIODS = {
    "P1W": "ONE_WEEK",
    "P1M": "ONE_MONTH",
    "P2M": "TWO_MONTHS",
    "P3M": "THREE_MONTHS",
    "P6M": "SIX_MONTHS",
    "P1Y": "ONE_YEAR",
}


def list_in_app_purchases(*, app_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    purchases = client.list_in_app_purchases(numeric_app_id)
    return {
        "purchases": [
            {"id": p.id, "name": p.name, "product_id": p.product_id, "type": p.in_app_purchase_type, "state": p.state}
            for p in purchases
        ]
    }


def create_in_app_purchase(
    *, app_id: str, name: str, product_id: str, in_app_purchase_type: str, review_note: str = ""
) -> dict[str, Any]:
    if in_app_purchase_type not in _IAP_TYPES:
        raise validation_error(
            f"in_app_purchase_type must be one of {sorted(_IAP_TYPES)}, got '{in_app_purchase_type}'",
            field_name="in_app_purchase_type",
        )
    if not name.strip() or not product_id.strip():
        raise validation_error("name and product_id must not be empty", field_name="name")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    iap_id = client.create_in_app_purchase(
        numeric_app_id,
        name=name.strip(),
        product_id=product_id.strip(),
        in_app_purchase_type=in_app_purchase_type,
        review_note=review_note,
    )
    return {"id": iap_id}


def delete_in_app_purchase(*, iap_id: str) -> dict[str, Any]:
    """Delete an in-app purchase. Apple only allows this while it has never been approved (a
    DRAFT/MISSING_METADATA record); an already-approved IAP can't be deleted via the API at all
    and must be removed from sale in the ASC web UI. Same account-migration prerequisite as
    list_in_app_purchases — raises a typed `IAP_ACCOUNT_NOT_MIGRATED` error otherwise."""
    if not iap_id.strip():
        raise validation_error("iap_id must not be empty", field_name="iap_id")
    client = context.get_asc_client()
    client.delete_in_app_purchase(iap_id.strip())
    return {"deleted": True}


def set_in_app_purchase_price(*, iap_id: str, price_point_id: str, territory: str = "USA") -> dict[str, Any]:
    client = context.get_asc_client()
    client.set_in_app_purchase_price(iap_id, price_point_id=price_point_id, territory=territory)
    return {"set": True, "price_point_id": price_point_id, "territory": territory}


def list_subscription_groups(*, app_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    groups = client.list_subscription_groups(numeric_app_id)
    return {"groups": [{"id": g.id, "reference_name": g.reference_name} for g in groups]}


def list_subscriptions(*, group_id: str) -> dict[str, Any]:
    if not group_id.strip():
        raise validation_error("group_id must not be empty", field_name="group_id")
    client = context.get_asc_client()
    subscriptions = client.list_subscriptions(group_id)
    return {
        "subscriptions": [
            {"id": s.id, "name": s.name, "product_id": s.product_id, "subscription_period": s.subscription_period, "state": s.state}
            for s in subscriptions
        ]
    }


def get_setup_capabilities(*, app_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "app_creation": {"automatable": False, "requires": "App Store Connect web UI", "reason": "Apple does not expose an API to create app records."},
        "subscription_setup": {"automatable": True, "requires": "existing app opted into API-based IAP management"},
        "revenuecat_mapping": {"automatable": False, "requires": "RevenueCat dashboard or RevenueCat API credentials"},
        "subscription_review_screenshot": {"automatable": False, "requires": "App Store Connect web UI or a supported subscription asset upload path"},
        "next_action": "Create the app record in App Store Connect, then enable API-based In-App Purchases and Subscriptions from its Features tab.",
    }
    if app_id:
        client = context.get_asc_client()
        try:
            app = client.find_app_by_bundle_id(app_id)
        except ASOgenicError as exc:
            if exc.code != "APP_NOT_FOUND":
                raise
            result["app"] = {"app_id": app_id, "numeric_id": None, "exists": False}
            result["next_action"] = "Create the app record in App Store Connect, then call this tool again."
            return result
        result["app"] = {"app_id": app_id, "numeric_id": app.id, "exists": True}
        try:
            client.list_subscription_groups(app.id)
            result["subscription_setup"]["api_access"] = "available"
            result["next_action"] = "Call asogenic_setup_subscriptions with the desired product IDs and prices."
        except ASOgenicError as exc:
            if getattr(exc, "code", "") == "IAP_ACCOUNT_NOT_MIGRATED":
                result["subscription_setup"]["api_access"] = "migration_required"
                result["next_action"] = "Enable API-based In-App Purchases and Subscriptions in App Store Connect, then retry."
            else:
                raise
    return result


def setup_subscriptions(
    *, app_id: str, group_reference_name: str, monthly_product_id: str, annual_product_id: str,
    monthly_name: str = "Monthly", annual_name: str = "Annual", locale: str = "en-US",
    monthly_description: str = "Monthly access", annual_description: str = "Annual access",
    monthly_price: str | None = None, annual_price: str | None = None, territory: str = "USA",
    review_note: str = "",
) -> dict[str, Any]:
    """Idempotently prepare a monthly and annual subscription pair for an existing app.

    Product IDs are required inputs because Apple requires the developer to choose them. Existing
    products are resolved by product ID; missing products are created. Pricing is optional because
    setting a price is a production mutation and should be explicit.
    """
    if not group_reference_name.strip() or not monthly_product_id.strip() or not annual_product_id.strip():
        raise validation_error("group_reference_name and both product IDs must not be empty", field_name="group_reference_name")
    if monthly_product_id.strip() == annual_product_id.strip():
        raise validation_error("monthly_product_id and annual_product_id must be different", field_name="annual_product_id")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    groups = client.list_subscription_groups(numeric_app_id)
    group = next((g for g in groups if g.reference_name == group_reference_name.strip()), None)
    group_created = False
    if group is None:
        group_id = client.create_subscription_group(numeric_app_id, reference_name=group_reference_name.strip())
        group_created = True
    else:
        group_id = group.id
    existing = {s.product_id: s for s in client.list_subscriptions(group_id)}
    specs = [
        ("monthly", monthly_product_id.strip(), monthly_name, "P1M", monthly_description, monthly_price),
        ("annual", annual_product_id.strip(), annual_name, "P1Y", annual_description, annual_price),
    ]
    products: list[dict[str, Any]] = []
    for kind, product_id, name, period, description, price in specs:
        current = existing.get(product_id)
        created = False
        if current is None:
            subscription_id = client.create_subscription(group_id, name=name.strip(), product_id=product_id, subscription_period=_SUBSCRIPTION_PERIODS[period], review_note=review_note)
            created = True
        else:
            subscription_id = current.id
        client.set_subscription_localization(subscription_id, locale=locale, name=name.strip(), description=description)
        price_result: dict[str, Any] | None = None
        if price is not None:
            points = client.list_subscription_price_points(subscription_id, territory=territory)
            match = next((point for point in points if point.customer_price == str(price)), None)
            if match is None:
                raise validation_error(f"No Apple price point matches {price} in {territory} for {kind}", field_name=f"{kind}_price")
            client.set_subscription_price(subscription_id, price_point_id=match.id, territory=territory)
            price_result = {"customer_price": match.customer_price, "price_point_id": match.id, "territory": territory}
        products.append({"kind": kind, "id": subscription_id, "product_id": product_id, "created": created, "price": price_result})
    return {
        "app_id": app_id, "group": {"id": group_id, "reference_name": group_reference_name.strip(), "created": group_created},
        "subscriptions": products,
        "next_actions": [
            "Upload the subscription review screenshot in App Store Connect before submitting for review.",
            "Map the exact product_id values in RevenueCat if RevenueCat is being used.",
        ],
    }


def create_subscription_group(*, app_id: str, reference_name: str) -> dict[str, Any]:
    if not reference_name.strip():
        raise validation_error("reference_name must not be empty", field_name="reference_name")
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    group_id = client.create_subscription_group(numeric_app_id, reference_name=reference_name.strip())
    return {"id": group_id}


def delete_subscription_group(*, group_id: str) -> dict[str, Any]:
    """Delete a subscription group. Apple rejects this while the group still contains any
    subscription — delete those first with delete_subscription."""
    if not group_id.strip():
        raise validation_error("group_id must not be empty", field_name="group_id")
    client = context.get_asc_client()
    client.delete_subscription_group(group_id.strip())
    return {"deleted": True}


def create_subscription(
    *, group_id: str, name: str, product_id: str, subscription_period: str, review_note: str = ""
) -> dict[str, Any]:
    if subscription_period not in _SUBSCRIPTION_PERIODS:
        raise validation_error(
            f"subscription_period must be one of {sorted(_SUBSCRIPTION_PERIODS)}, got '{subscription_period}'",
            field_name="subscription_period",
        )
    if not name.strip() or not product_id.strip():
        raise validation_error("name and product_id must not be empty", field_name="name")
    client = context.get_asc_client()
    subscription_id = client.create_subscription(
        group_id,
        name=name.strip(),
        product_id=product_id.strip(),
        subscription_period=_SUBSCRIPTION_PERIODS[subscription_period],
        review_note=review_note,
    )
    return {"id": subscription_id}


def delete_subscription(*, subscription_id: str) -> dict[str, Any]:
    """Delete a subscription. Apple only allows this while it has never been approved; an
    already-approved subscription can't be deleted via the API and must be removed from sale in
    the ASC web UI."""
    if not subscription_id.strip():
        raise validation_error("subscription_id must not be empty", field_name="subscription_id")
    client = context.get_asc_client()
    client.delete_subscription(subscription_id.strip())
    return {"deleted": True}


def list_subscription_price_points(
    *, subscription_id: str, territory: str = "USA", customer_price: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Subscription price points are a distinct Apple resource collection from an app's own
    `appPricePoints` (asogenic_list_price_points) — required because asogenic_set_subscription_price
    rejects an app price-point id with `ENTITY_ERROR.RELATIONSHIP.INVALID`. Same
    filter/truncation shape as asogenic_list_price_points: pass `customer_price` to look up one
    exact point, otherwise the first `limit` are returned with `total_count`/`truncated`."""
    client = context.get_asc_client()
    points = client.list_subscription_price_points(subscription_id, territory=territory)
    if customer_price is not None:
        points = [p for p in points if p.customer_price == customer_price]
    total_count = len(points)
    truncated = customer_price is None and total_count > limit
    points = points[:limit] if customer_price is None else points
    return {
        "price_points": [{"id": p.id, "customer_price": p.customer_price, "territory": p.territory} for p in points],
        "total_count": total_count,
        "truncated": truncated,
    }


def set_subscription_price(*, subscription_id: str, price_point_id: str, territory: str = "USA") -> dict[str, Any]:
    client = context.get_asc_client()
    client.set_subscription_price(subscription_id, price_point_id=price_point_id, territory=territory)
    return {"set": True, "price_point_id": price_point_id, "territory": territory}


def set_subscription_localization(
    *, subscription_id: str, locale: str, name: str, description: str = ""
) -> dict[str, Any]:
    """The subscription's display name/description for one locale — what a user actually sees on
    the paywall, not internal bookkeeping. Create-or-update: the first call for a given locale
    creates the record, every call after that updates it in place."""
    if not locale.strip():
        raise validation_error("locale must not be empty", field_name="locale")
    if not name.strip():
        raise validation_error("name must not be empty", field_name="name")
    client = context.get_asc_client()
    client.set_subscription_localization(subscription_id, locale=locale, name=name.strip(), description=description)
    return {"set": True}
