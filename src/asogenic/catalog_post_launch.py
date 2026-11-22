"""Public-safe TestFlight, customer review, and monetization MCP catalog."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import monetization, reviews, testflight

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_post_launch_catalog(server: FastMCP) -> None:
    """Register TestFlight, customer review, and monetization tools."""
    # --- TestFlight: beta groups, testers, build notes, external beta review ----------------------

    @server.tool(name="asogenic_list_beta_groups", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_beta_groups")
    def asogenic_list_beta_groups(app_id: str) -> dict[str, Any]:
        """Every TestFlight beta group for this app. Call this to find a `group_id` for
        asogenic_invite_beta_tester / asogenic_list_beta_testers.

        Returns `{"groups": [{id, name, is_internal, public_link_enabled}, ...]}`. `is_internal`
        groups (App Store Connect users on the team) never need asogenic_submit_beta_review — only
        external groups do."""
        return testflight.list_beta_groups(app_id=app_id)


    @server.tool(
        name="asogenic_create_beta_group",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_beta_group")
    def asogenic_create_beta_group(app_id: str, name: str) -> dict[str, Any]:
        """Create a new external TestFlight beta group for this app (internal groups are managed by
        App Store Connect team membership, not this tool).

        Returns `{"id": str}` — the new group's id."""
        return testflight.create_beta_group(app_id=app_id, name=name)


    @server.tool(
        name="asogenic_delete_beta_group",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_beta_group")
    def asogenic_delete_beta_group(group_id: str) -> dict[str, Any]:
        """Delete an external TestFlight beta group. Testers who could install a build only through
        this group lose that access (testers in other groups keep theirs). Internal groups are
        managed by App Store Connect team membership and can't be deleted here.

        Returns `{"deleted": True}`."""
        return testflight.delete_beta_group(group_id=group_id)


    @server.tool(name="asogenic_list_beta_testers", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_beta_testers")
    def asogenic_list_beta_testers(app_id: str, group_id: str | None = None) -> dict[str, Any]:
        """Testers on this app, or just one group's testers if `group_id` is given.

        Returns `{"testers": [{id, email, first_name, last_name}, ...]}`."""
        return testflight.list_beta_testers(app_id=app_id, group_id=group_id)


    @server.tool(
        name="asogenic_invite_beta_tester",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_invite_beta_tester")
    def asogenic_invite_beta_tester(
        group_id: str, email: str, first_name: str = "", last_name: str = ""
    ) -> dict[str, Any]:
        """Create a tester and add them to `group_id` in one call — Apple sends the invite email as a
        side effect of this, not a separate step.

        Returns `{"id": str, "invited": True}`."""
        return testflight.invite_beta_tester(group_id=group_id, email=email, first_name=first_name, last_name=last_name)


    @server.tool(
        name="asogenic_remove_beta_tester",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_remove_beta_tester")
    def asogenic_remove_beta_tester(tester_id: str, group_id: str | None = None) -> dict[str, Any]:
        """Remove a tester from `group_id` if given, else delete the tester outright — removing them
        from every group and revoking their TestFlight access to this app entirely.

        Returns `{"removed": True}`."""
        return testflight.remove_beta_tester(tester_id=tester_id, group_id=group_id)


    @server.tool(
        name="asogenic_set_beta_build_notes",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_beta_build_notes")
    def asogenic_set_beta_build_notes(build_id: str, locale: str, whats_new: str) -> dict[str, Any]:
        """The beta-facing "what to test" notes for one build+locale — separate from the App Store
        version's whatsNew field (asogenic_publish); a build can be in TestFlight well before any App
        Store text metadata is ready. Create-or-update: safe to call again to revise.

        Returns `{"set": True}`."""
        return testflight.set_beta_build_notes(build_id=build_id, locale=locale, whats_new=whats_new)


    @server.tool(name="asogenic_list_beta_app_localizations", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_beta_app_localizations")
    def asogenic_list_beta_app_localizations(app_id: str) -> dict[str, Any]:
        """List localized TestFlight app information, including the beta app description required
        before external beta review submission."""
        return testflight.list_beta_app_localizations(app_id=app_id)


    @server.tool(
        name="asogenic_set_beta_app_localization",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_beta_app_localization")
    def asogenic_set_beta_app_localization(
        app_id: str,
        locale: str,
        description: str,
        feedback_email: str | None = None,
        marketing_url: str | None = None,
        privacy_policy_url: str | None = None,
    ) -> dict[str, Any]:
        """Create or update localized TestFlight app information. Apple requires a non-empty
        description for every beta app localization before `asogenic_submit_beta_review` succeeds."""
        return testflight.set_beta_app_localization(
            app_id=app_id,
            locale=locale,
            description=description,
            feedback_email=feedback_email,
            marketing_url=marketing_url,
            privacy_policy_url=privacy_policy_url,
        )


    @server.tool(
        name="asogenic_submit_beta_review",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_submit_beta_review")
    def asogenic_submit_beta_review(build_id: str) -> dict[str, Any]:
        """Submit a build for Apple's external-beta review — required before testers in any
        non-internal group can install it. Internal-group testers never need this.

        Returns `{"submitted": True, "submission_id": str}`. Check
        asogenic_get_beta_review_status for the outcome."""
        return testflight.submit_beta_review(build_id=build_id)


    @server.tool(name="asogenic_get_beta_review_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_beta_review_status")
    def asogenic_get_beta_review_status(build_id: str) -> dict[str, Any]:
        """Apple's own external-beta-review state for one build.

        Returns `{"state": str | None}` — `state` is one of Apple's own values (e.g. `"WAITING_FOR_REVIEW"`,
        `"IN_REVIEW"`, `"APPROVED"`, `"REJECTED"`) or `None` if the build was never submitted for beta
        review."""
        return testflight.get_beta_review_status(build_id=build_id)


    # --- Customer reviews: list and reply ----------------------------------------------------------

    @server.tool(name="asogenic_list_customer_reviews", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_customer_reviews")
    def asogenic_list_customer_reviews(app_id: str, territory: str | None = None) -> dict[str, Any]:
        """Customer reviews for this app, optionally filtered to one storefront territory (e.g.
        `"USA"`).

        Returns `{"reviews": [{id, rating, title, body, reviewer_nickname, territory, created_date,
        response_id}, ...]}`. `response_id` is null if nobody has replied yet — filter on that
        instead of a separate call per review to find unanswered ones."""
        return reviews.list_customer_reviews(app_id=app_id, territory=territory)


    @server.tool(
        name="asogenic_reply_to_review",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_reply_to_review")
    def asogenic_reply_to_review(review_id: str, response_body: str) -> dict[str, Any]:
        """Post a developer response to one customer review. Apple's API has no PATCH for review
        responses — CREATE and DELETE are the only operations it allows, so replying to a review that
        already has a response fails. To edit a reply: asogenic_delete_review_response the existing
        one first (asogenic_list_customer_reviews' `response_id` tells you it exists), then call this
        again.

        Returns `{"id": str}` — the new response's id."""
        return reviews.reply_to_review(review_id=review_id, response_body=response_body)


    @server.tool(
        name="asogenic_delete_review_response",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_review_response")
    def asogenic_delete_review_response(response_id: str) -> dict[str, Any]:
        """Remove a developer response from a review — the only way to change one, since
        asogenic_reply_to_review can't overwrite an existing response.

        Returns `{"deleted": True}`."""
        return reviews.delete_review_response(response_id=response_id)


    # --- In-app purchases & subscriptions -----------------------------------------------------------

    @server.tool(name="asogenic_list_in_app_purchases", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_in_app_purchases")
    def asogenic_list_in_app_purchases(app_id: str) -> dict[str, Any]:
        """Every in-app purchase for this app.

        Raises a typed `IAP_ACCOUNT_NOT_MIGRATED` error for an app that has never had its in-app
        purchases migrated to API-based management — Apple requires a one-time, irreversible "Manage
        In-App Purchases and Subscriptions via the App Store Connect API" migration per app (done
        from the app's Features tab in the ASC web UI) before any `inAppPurchasesV2`/`subscriptions`
        endpoint works. See asogenic.errors.iap_not_migrated_error's docstring for a confirmed-live
        correction: `PARAMETER_ERROR.INVALID 'inAppPurchasesV2' is not a valid type name` was
        previously (mis)cited as proof of this — it was actually this server's own wrong
        sparse-fieldset parameter name, now fixed, and not evidence of an unmigrated app on its own.

        Returns `{"purchases": [{id, name, product_id, type, state}, ...]}`."""
        return monetization.list_in_app_purchases(app_id=app_id)


    @server.tool(
        name="asogenic_create_in_app_purchase",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_in_app_purchase")
    def asogenic_create_in_app_purchase(
        app_id: str, name: str, product_id: str, in_app_purchase_type: str, review_note: str = ""
    ) -> dict[str, Any]:
        """Create a new in-app purchase in DRAFT state. `in_app_purchase_type` is one of Apple's enum:
        `CONSUMABLE`, `NON_CONSUMABLE`, `NON_RENEWING_SUBSCRIPTION`. This does not submit it for
        Apple's review — that requires an uploaded review screenshot this tool surface has no path
        for (see tools/monetization.py's module docstring); use App Store Connect's web UI to finish
        submission.

        Same account-migration prerequisite as asogenic_list_in_app_purchases: raises a typed
        `IAP_ACCOUNT_NOT_MIGRATED` error until the app has opted into API-based IAP management via
        ASC's web UI.

        Returns `{"id": str}`."""
        return monetization.create_in_app_purchase(
            app_id=app_id, name=name, product_id=product_id, in_app_purchase_type=in_app_purchase_type,
            review_note=review_note,
        )


    @server.tool(
        name="asogenic_delete_in_app_purchase",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_in_app_purchase")
    def asogenic_delete_in_app_purchase(iap_id: str) -> dict[str, Any]:
        """Delete an in-app purchase. Apple only allows this while the IAP has never been approved (a
        DRAFT/MISSING_METADATA record — e.g. one just created via asogenic_create_in_app_purchase); an
        already-approved IAP cannot be deleted through the API and has to be removed from sale in the
        App Store Connect web UI instead.

        Same account-migration prerequisite as asogenic_list_in_app_purchases: raises a typed
        `IAP_ACCOUNT_NOT_MIGRATED` error until the app has opted into API-based IAP management.

        Returns `{"deleted": True}`."""
        return monetization.delete_in_app_purchase(iap_id=iap_id)


    @server.tool(
        name="asogenic_set_in_app_purchase_price",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_set_in_app_purchase_price")
    def asogenic_set_in_app_purchase_price(iap_id: str, price_point_id: str, territory: str = "USA") -> dict[str, Any]:
        """Set an in-app purchase's price to one of Apple's price-point ids for `territory` (same
        price-point-id scheme as asogenic_set_base_price/asogenic_list_price_points, but for the
        in-app-purchase price-point collection, not the app's own).

        Returns `{"set": True, "price_point_id": str, "territory": str}`."""
        return monetization.set_in_app_purchase_price(iap_id=iap_id, price_point_id=price_point_id, territory=territory)


    @server.tool(name="asogenic_list_subscription_groups", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_subscription_groups")
    def asogenic_list_subscription_groups(app_id: str) -> dict[str, Any]:
        """Every subscription group for this app. Call this to find a `group_id` for
        asogenic_create_subscription.

        Returns `{"groups": [{id, reference_name}, ...]}`."""
        return monetization.list_subscription_groups(app_id=app_id)


    @server.tool(name="asogenic_get_setup_capabilities", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_setup_capabilities")
    def asogenic_get_setup_capabilities(app_id: str | None = None) -> dict[str, Any]:
        """Explain what ASOgenic can automate for a new-app monetization setup and what remains
        manual. Apple app-record creation is intentionally reported as a UI prerequisite because the
        App Store Connect API does not provide a create-app endpoint. Pass an existing bundle ID to
        check whether its subscription API access is enabled."""
        return monetization.get_setup_capabilities(app_id=app_id)


    @server.tool(name="asogenic_list_subscriptions", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_subscriptions")
    def asogenic_list_subscriptions(group_id: str) -> dict[str, Any]:
        """List every subscription in a group, including the exact Apple product IDs. This removes
        the need to infer IDs from duplicate-create errors or copy them manually from App Store Connect."""
        return monetization.list_subscriptions(group_id=group_id)


    @server.tool(
        name="asogenic_setup_subscriptions",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_setup_subscriptions")
    def asogenic_setup_subscriptions(
        app_id: str, group_reference_name: str, monthly_product_id: str, annual_product_id: str,
        monthly_name: str = "Monthly", annual_name: str = "Annual", locale: str = "en-US",
        monthly_description: str = "Monthly access", annual_description: str = "Annual access",
        monthly_price: str | None = None, annual_price: str | None = None, territory: str = "USA",
        review_note: str = "",
    ) -> dict[str, Any]:
        """Prepare a complete monthly/annual subscription setup for an existing App Store Connect
        app: resolve or create the group, resolve or create both products, set localization, and
        optionally set exact Apple price points. Product IDs are returned exactly. This is idempotent
        for matching product IDs. It cannot create the app record, enable Apple's one-time API
        migration, configure RevenueCat, or upload the subscription review screenshot; the result's
        next_actions states those remaining steps."""
        return monetization.setup_subscriptions(
            app_id=app_id, group_reference_name=group_reference_name, monthly_product_id=monthly_product_id,
            annual_product_id=annual_product_id, monthly_name=monthly_name, annual_name=annual_name,
            locale=locale, monthly_description=monthly_description, annual_description=annual_description,
            monthly_price=monthly_price, annual_price=annual_price, territory=territory, review_note=review_note,
        )


    @server.tool(
        name="asogenic_create_subscription_group",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_subscription_group")
    def asogenic_create_subscription_group(app_id: str, reference_name: str) -> dict[str, Any]:
        """Create a new subscription group — the container every individual subscription
        (asogenic_create_subscription) belongs to. `reference_name` is internal-only, never shown to
        users.

        Returns `{"id": str}`."""
        return monetization.create_subscription_group(app_id=app_id, reference_name=reference_name)


    @server.tool(
        name="asogenic_delete_subscription_group",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_subscription_group")
    def asogenic_delete_subscription_group(group_id: str) -> dict[str, Any]:
        """Delete a subscription group. Apple rejects this while the group still contains any
        subscription — delete those first with asogenic_delete_subscription.

        Returns `{"deleted": True}`."""
        return monetization.delete_subscription_group(group_id=group_id)


    @server.tool(
        name="asogenic_create_subscription",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_subscription")
    def asogenic_create_subscription(
        group_id: str, name: str, product_id: str, subscription_period: str, review_note: str = ""
    ) -> dict[str, Any]:
        """Create a new subscription in a group. `subscription_period` is one of Apple's enum: `P1W`,
        `P1M`, `P2M`, `P3M`, `P6M`, `P1Y`. `name` here is internal (App Store Connect-only); the
        user-facing display name is set separately via asogenic_set_subscription_localization. Does
        not submit for Apple's review — see asogenic_create_in_app_purchase's docstring for why.

        Returns `{"id": str}`."""
        return monetization.create_subscription(
            group_id=group_id, name=name, product_id=product_id, subscription_period=subscription_period,
            review_note=review_note,
        )


    @server.tool(
        name="asogenic_delete_subscription",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_subscription")
    def asogenic_delete_subscription(subscription_id: str) -> dict[str, Any]:
        """Delete a subscription. Apple only allows this while it has never been approved; an
        already-approved subscription can't be deleted through the API and has to be removed from
        sale in the App Store Connect web UI instead.

        Returns `{"deleted": True}`."""
        return monetization.delete_subscription(subscription_id=subscription_id)


    @server.tool(name="asogenic_list_subscription_price_points", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_subscription_price_points")
    def asogenic_list_subscription_price_points(
        subscription_id: str, territory: str = "USA", customer_price: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Available Apple price-point ids for one subscription+territory — call this to find a
        `price_point_id` for asogenic_set_subscription_price. Confirmed live: an `appPricePoints`
        id from asogenic_list_price_points does NOT work here — subscriptions have their own,
        separate price-point collection, and feeding in an app price-point id fails with
        `ENTITY_ERROR.RELATIONSHIP.INVALID`. Pass `customer_price` (e.g. "0.99") to look up one
        exact point; otherwise the first `limit` points are returned in Apple's own ascending-price
        order, along with `total_count`/`truncated`.

        Returns `{"price_points": [{id, customer_price, territory}, ...], "total_count": int,
        "truncated": bool}`."""
        return monetization.list_subscription_price_points(
            subscription_id=subscription_id, territory=territory, customer_price=customer_price, limit=limit
        )


    @server.tool(
        name="asogenic_set_subscription_price",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_set_subscription_price")
    def asogenic_set_subscription_price(
        subscription_id: str, price_point_id: str, territory: str = "USA"
    ) -> dict[str, Any]:
        """Set a subscription's price to one of Apple's subscription price-point ids for `territory`
        — get one from asogenic_list_subscription_price_points, NOT asogenic_list_price_points
        (those are a different, incompatible id collection).

        Picks the right Apple flow automatically: a subscription's *first* price ever goes through
        `PATCH /v1/subscriptions/{id}` with an inline price resource (Apple's only supported way to
        set an initial price); a price *change* afterward goes through `POST /v1/subscriptionPrices`.
        Confirmed live: using the POST flow for a subscription's first price 400s with
        `ENTITY_ERROR.RELATIONSHIP.INVALID` even with fully valid ids — this tool checks for an
        existing price first so callers don't have to know which flow applies.

        Returns `{"set": True, "price_point_id": str, "territory": str}`."""
        return monetization.set_subscription_price(
            subscription_id=subscription_id, price_point_id=price_point_id, territory=territory
        )


    @server.tool(
        name="asogenic_set_subscription_localization",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_subscription_localization")
    def asogenic_set_subscription_localization(
        subscription_id: str, locale: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        """The subscription's user-facing display name/description for one locale — what actually
        shows on the paywall, the ASO-relevant piece of this tool surface. Create-or-update: safe to
        call again to revise.

        Returns `{"set": True}`."""
        return monetization.set_subscription_localization(
            subscription_id=subscription_id, locale=locale, name=name, description=description
        )
