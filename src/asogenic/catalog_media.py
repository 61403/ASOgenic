"""Public-safe Custom Product Pages, media, screenshots, and optimization tools.

The runtime checks in the shared media I/O layer allow filesystem paths only when
LocalRuntime advertises that capability. This catalog contains no hosted administration.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import media_specs, previews, product_pages, screenshots

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_media_catalog(server: FastMCP) -> None:
    """Register product pages, reusable media, previews, PPO, and screenshot tools."""

    # --- Product pages, Asset Library, and Product Page Optimization -----------------------------

    @server.tool(name="asogenic_list_custom_product_pages", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_custom_product_pages")
    def asogenic_list_custom_product_pages(app_id: str) -> dict[str, Any]:
        """List an app's Custom Product Pages, including their stable URLs and visibility."""
        return product_pages.list_custom_product_pages(app_id=app_id)

    @server.tool(
        name="asogenic_create_custom_product_page",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_custom_product_page")
    def asogenic_create_custom_product_page(
        app_id: str,
        name: str,
        template_version_id: str | None = None,
        locale: str = "en-US",
        promotional_text: str = "",
    ) -> dict[str, Any]:
        """Create a Custom Product Page. Apple permits up to 70 pages per app; submit its version
        for review before the page is visible. Optionally copy an existing app-store version by id."""
        return product_pages.create_custom_product_page(
            app_id=app_id,
            name=name,
            template_version_id=template_version_id,
            locale=locale,
            promotional_text=promotional_text,
        )

    @server.tool(
        name="asogenic_update_custom_product_page",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_update_custom_product_page")
    def asogenic_update_custom_product_page(
        page_id: str, visible: bool | None = None, name: str | None = None
    ) -> dict[str, Any]:
        """Update a custom page's reference name or visibility. Visibility changes do not alter its URL."""
        return product_pages.update_custom_product_page(page_id=page_id, visible=visible, name=name)

    @server.tool(
        name="asogenic_delete_custom_product_page",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_custom_product_page")
    def asogenic_delete_custom_product_page(page_id: str) -> dict[str, Any]:
        """Permanently delete a Custom Product Page. Customers using its URL fall back to the default page."""
        return product_pages.delete_custom_product_page(page_id=page_id)

    @server.tool(
        name="asogenic_create_custom_product_page_version",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_custom_product_page_version")
    def asogenic_create_custom_product_page_version(
        page_id: str, deep_link: str | None = None
    ) -> dict[str, Any]:
        """Create a draft version for a custom page, optionally with an iOS 18+ deep link."""
        return product_pages.create_custom_product_page_version(
            page_id=page_id, deep_link=deep_link
        )

    @server.tool(name="asogenic_list_custom_product_page_localizations", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_custom_product_page_localizations")
    def asogenic_list_custom_product_page_localizations(version_id: str) -> dict[str, Any]:
        return product_pages.list_custom_product_page_localizations(version_id=version_id)

    @server.tool(
        name="asogenic_create_custom_product_page_localization",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_custom_product_page_localization")
    def asogenic_create_custom_product_page_localization(
        version_id: str, locale: str, promotional_text: str = ""
    ) -> dict[str, Any]:
        return product_pages.create_custom_product_page_localization(
            version_id=version_id, locale=locale, promotional_text=promotional_text
        )

    @server.tool(
        name="asogenic_update_custom_product_page_localization",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_update_custom_product_page_localization")
    def asogenic_update_custom_product_page_localization(
        localization_id: str, promotional_text: str
    ) -> dict[str, Any]:
        return product_pages.update_custom_product_page_localization(
            localization_id=localization_id, promotional_text=promotional_text
        )

    @server.tool(
        name="asogenic_delete_custom_product_page_localization",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_custom_product_page_localization")
    def asogenic_delete_custom_product_page_localization(localization_id: str) -> dict[str, Any]:
        return product_pages.delete_custom_product_page_localization(
            localization_id=localization_id
        )

    @server.tool(name="asogenic_list_custom_product_page_keywords", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_custom_product_page_keywords")
    def asogenic_list_custom_product_page_keywords(localization_id: str) -> dict[str, Any]:
        return product_pages.list_custom_page_keywords(localization_id=localization_id)

    @server.tool(
        name="asogenic_set_custom_product_page_keywords",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_custom_product_page_keywords")
    def asogenic_set_custom_product_page_keywords(
        localization_id: str, keyword_ids: list[str]
    ) -> dict[str, Any]:
        return product_pages.set_custom_page_keywords(
            localization_id=localization_id, keyword_ids=keyword_ids
        )

    @server.tool(
        name="asogenic_remove_custom_product_page_keywords",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_remove_custom_product_page_keywords")
    def asogenic_remove_custom_product_page_keywords(
        localization_id: str, keyword_ids: list[str]
    ) -> dict[str, Any]:
        return product_pages.remove_custom_page_keywords(
            localization_id=localization_id, keyword_ids=keyword_ids
        )

    @server.tool(name="asogenic_get_asset_library", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_asset_library")
    def asogenic_get_asset_library(app_id: str) -> dict[str, Any]:
        """Return the stable Asset Library resource for an app."""
        return product_pages.list_asset_library(app_id=app_id)

    @server.tool(name="asogenic_list_asset_library_assets", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_asset_library_assets")
    def asogenic_list_asset_library_assets(
        library_id: str,
        kind: str = "image",
        category: str | None = None,
        state: str | None = None,
        spec_id: str | None = None,
    ) -> dict[str, Any]:
        """List reusable Asset Library images or videos. Use Apple's ref-data tool to discover
        exact dimensions and the new IPHONE_DUO-compatible specifications instead of hard-coding sizes."""
        return product_pages.list_asset_library_assets(
            library_id=library_id, kind=kind, category=category, state=state, spec_id=spec_id
        )

    @server.tool(name="asogenic_get_asset_library_reference_data", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_asset_library_reference_data")
    def asogenic_get_asset_library_reference_data() -> dict[str, Any]:
        """Return Apple's current Asset Library specs, placements, and size mappings."""
        return product_pages.get_asset_library_reference_data()

    @server.tool(name="asogenic_get_media_specs", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_media_specs")
    def asogenic_get_media_specs() -> dict[str, Any]:
        """Return Apple's current Asset Library media reference data and ASOgenic's stable media
        capability envelope. Call this before selecting a device size, preview type, or creative slot;
        Apple can add identifiers after this server is released."""
        return media_specs.get_media_specs()

    @server.tool(
        name="asogenic_upload_asset_library_asset",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_upload_asset_library_asset")
    def asogenic_upload_asset_library_asset(
        library_id: str,
        kind: str,
        category: str,
        file_name: str,
        asset_base64: str | None = None,
        asset_path: str | None = None,
        reference_name: str | None = None,
    ) -> dict[str, Any]:
        """Reserve, stream, commit, and verify an Asset Library image or video.

        Provide exactly one of ``asset_base64`` or ``asset_path``. Local paths are useful for large
        videos in stdio/local deployments. Hosted callers cannot access their own filesystem: use
        Base64 only for media within the request limit, or run the local stdio server. A hosted
        staged-upload session is not available yet.
        """
        return product_pages.upload_asset_library_asset(
            library_id=library_id,
            kind=kind,
            category=category,
            file_name=file_name,
            asset_base64=asset_base64,
            asset_path=asset_path,
            reference_name=reference_name,
        )

    @server.tool(
        name="asogenic_update_asset_library_asset",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_update_asset_library_asset")
    def asogenic_update_asset_library_asset(
        kind: str,
        asset_id: str,
        reference_name: str | None = None,
        archived: bool | None = None,
        poster_frame_time_code: str | None = None,
    ) -> dict[str, Any]:
        return product_pages.update_asset_library_asset(
            kind=kind,
            asset_id=asset_id,
            reference_name=reference_name,
            archived=archived,
            poster_frame_time_code=poster_frame_time_code,
        )

    @server.tool(name="asogenic_get_asset_library_asset_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_asset_library_asset_status")
    def asogenic_get_asset_library_asset_status(kind: str, asset_id: str) -> dict[str, Any]:
        """Read Apple processing, review, specification, and submission state for one library asset."""
        return product_pages.get_asset_library_asset_status(kind=kind, asset_id=asset_id)

    @server.tool(
        name="asogenic_delete_asset_library_asset",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_asset_library_asset")
    def asogenic_delete_asset_library_asset(kind: str, asset_id: str) -> dict[str, Any]:
        """Delete a prepared-for-submission Asset Library asset; archive it instead once submitted."""
        return product_pages.delete_asset_library_asset(kind=kind, asset_id=asset_id)

    @server.tool(name="asogenic_list_asset_library_placements", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_asset_library_placements")
    def asogenic_list_asset_library_placements(
        localization_id: str, localization_type: str = "appStoreVersionLocalizations"
    ) -> dict[str, Any]:
        return product_pages.list_placements(
            localization_id=localization_id, localization_type=localization_type
        )

    @server.tool(
        name="asogenic_create_asset_library_placement",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_asset_library_placement")
    def asogenic_create_asset_library_placement(
        asset_id: str,
        asset_kind: str,
        placement_type: str,
        placement_group: str,
        localization_id: str,
        localization_type: str = "appStoreVersionLocalizations",
    ) -> dict[str, Any]:
        return product_pages.create_placement(
            asset_id=asset_id,
            asset_kind=asset_kind,
            placement_type=placement_type,
            placement_group=placement_group,
            localization_id=localization_id,
            localization_type=localization_type,
        )

    @server.tool(
        name="asogenic_delete_asset_library_placement",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_asset_library_placement")
    def asogenic_delete_asset_library_placement(placement_id: str) -> dict[str, Any]:
        return product_pages.delete_placement(placement_id=placement_id)

    @server.tool(
        name="asogenic_reorder_asset_library_placements",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_reorder_asset_library_placements")
    def asogenic_reorder_asset_library_placements(
        localization_id: str,
        placement_group: str,
        placement_ids: list[str],
        localization_type: str = "appStoreVersionLocalizations",
    ) -> dict[str, Any]:
        """Replace one Asset Library placement group's order, including PPO treatment targets."""
        return product_pages.reorder_placements(
            localization_id=localization_id,
            placement_group=placement_group,
            placement_ids=placement_ids,
            localization_type=localization_type,
        )

    @server.tool(name="asogenic_list_app_previews", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_app_previews")
    def asogenic_list_app_previews(app_id: str, platform: str, locale: str) -> dict[str, Any]:
        """List app preview sets and ordered preview ids for a version localization."""
        return previews.list_previews(app_id=app_id, platform=platform, locale=locale)

    @server.tool(
        name="asogenic_upload_app_preview",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_upload_app_preview")
    def asogenic_upload_app_preview(
        app_id: str,
        platform: str,
        locale: str,
        preview_type: str,
        file_name: str,
        preview_base64: str | None = None,
        preview_path: str | None = None,
        poster_frame_time_code: str | None = None,
    ) -> dict[str, Any]:
        """Upload an App Store preview video from exactly one Base64 value or local file path.
        Local paths allow large videos without embedding them in an MCP JSON request. They work
        only in stdio/local deployments; hosted callers must use Base64 within the request limit.
        A hosted staged-upload session is not available yet."""
        return previews.upload_preview(
            app_id=app_id,
            platform=platform,
            locale=locale,
            preview_type=preview_type,
            file_name=file_name,
            preview_base64=preview_base64,
            preview_path=preview_path,
            poster_frame_time_code=poster_frame_time_code,
        )

    @server.tool(name="asogenic_get_app_preview_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_app_preview_status")
    def asogenic_get_app_preview_status(preview_id: str) -> dict[str, Any]:
        return previews.get_preview_status(preview_id=preview_id)

    @server.tool(
        name="asogenic_set_app_preview_poster_frame",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_set_app_preview_poster_frame")
    def asogenic_set_app_preview_poster_frame(
        preview_id: str, poster_frame_time_code: str
    ) -> dict[str, Any]:
        return previews.set_preview_poster_frame(
            preview_id=preview_id, poster_frame_time_code=poster_frame_time_code
        )

    @server.tool(
        name="asogenic_reorder_app_previews",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_reorder_app_previews")
    def asogenic_reorder_app_previews(
        preview_set_id: str, preview_ids: list[str]
    ) -> dict[str, Any]:
        return previews.reorder_previews(preview_set_id=preview_set_id, preview_ids=preview_ids)

    @server.tool(
        name="asogenic_delete_app_preview",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_app_preview")
    def asogenic_delete_app_preview(preview_id: str) -> dict[str, Any]:
        return previews.delete_preview(preview_id=preview_id)

    @server.tool(name="asogenic_list_product_page_optimization_tests", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_product_page_optimization_tests")
    def asogenic_list_product_page_optimization_tests(
        app_id: str, state: str | None = None
    ) -> dict[str, Any]:
        """List v2 Product Page Optimization tests and their treatments."""
        return product_pages.list_optimization_tests(app_id=app_id, state=state)

    @server.tool(
        name="asogenic_create_product_page_optimization_test",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_create_product_page_optimization_test")
    def asogenic_create_product_page_optimization_test(
        app_id: str, name: str, platform: str, traffic_proportion: int = 25
    ) -> dict[str, Any]:
        """Create a v2 PPO test for an app. Apple shows treatments to a defined random user group;
        custom pages are not eligible."""
        return product_pages.create_optimization_test(
            app_id=app_id, name=name, platform=platform, traffic_proportion=traffic_proportion
        )

    @server.tool(
        name="asogenic_update_product_page_optimization_test",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_update_product_page_optimization_test")
    def asogenic_update_product_page_optimization_test(
        test_id: str, started: bool | None = None, traffic_proportion: int | None = None
    ) -> dict[str, Any]:
        return product_pages.update_optimization_test(
            test_id=test_id, started=started, traffic_proportion=traffic_proportion
        )

    @server.tool(name="asogenic_list_product_page_optimization_treatments", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_product_page_optimization_treatments")
    def asogenic_list_product_page_optimization_treatments(test_id: str) -> dict[str, Any]:
        """List the treatments for one PPO test, including their localized treatment metadata."""
        return product_pages.list_optimization_treatments(test_id=test_id)

    @server.tool(name="asogenic_list_ppo_treatment_localizations", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_ppo_treatment_localizations")
    def asogenic_list_ppo_treatment_localizations(
        treatment_id: str, locale: str | None = None
    ) -> dict[str, Any]:
        """List localized targets for one PPO treatment, optionally filtered by locale."""
        return product_pages.list_treatment_localizations(treatment_id=treatment_id, locale=locale)

    @server.tool(
        name="asogenic_create_ppo_treatment_localization",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_create_ppo_treatment_localization")
    def asogenic_create_ppo_treatment_localization(
        treatment_id: str, locale: str
    ) -> dict[str, Any]:
        """Create or return a PPO treatment localization for a locale."""
        return product_pages.create_treatment_localization(treatment_id=treatment_id, locale=locale)

    @server.tool(
        name="asogenic_upload_ppo_treatment_asset",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    )
    @log_tool_call("asogenic_upload_ppo_treatment_asset")
    def asogenic_upload_ppo_treatment_asset(
        treatment_id: str,
        locale: str,
        library_id: str,
        kind: str,
        category: str,
        file_name: str,
        placement_type: str,
        placement_group: str,
        asset_base64: str | None = None,
        asset_path: str | None = None,
        reference_name: str | None = None,
        replace_placement_id: str | None = None,
    ) -> dict[str, Any]:
        """Upload an Asset Library image/video and place it on exactly one PPO treatment locale.

        ``asset_path`` is available only in stdio/local deployments. Hosted callers must use
        Base64 within the request limit; a hosted staged-upload session is not available yet.

        Set ``replace_placement_id`` to replace an existing treatment placement after the new
        asset is uploaded and placed. The default product-page localization is never targeted.
        """
        return product_pages.upload_ppo_treatment_asset(
            treatment_id=treatment_id,
            locale=locale,
            library_id=library_id,
            kind=kind,
            category=category,
            file_name=file_name,
            placement_type=placement_type,
            placement_group=placement_group,
            asset_base64=asset_base64,
            asset_path=asset_path,
            reference_name=reference_name,
            replace_placement_id=replace_placement_id,
        )

    @server.tool(name="asogenic_list_screenshots", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_screenshots")
    def asogenic_list_screenshots(app_id: str, platform: str, locale: str) -> dict[str, Any]:
        """Screenshot sets and screenshot ids currently on this locale's app store version — check
        this to see which device sizes already have a screenshot before uploading more.

        Returns `{"screenshot_sets": [{id, display_type, screenshot_ids}, ...]}` — one set per
        device-size (`display_type`, e.g. `"APP_IPHONE_67"`) that has at least one screenshot for
        this locale. This doesn't tell you whether Apple's asset pipeline actually accepted each
        screenshot — use asogenic_get_screenshot_status per id, or re-check
        asogenic_upload_screenshot's own `asset_delivery_state` at upload time."""
        return screenshots.list_screenshots(app_id=app_id, platform=platform, locale=locale)

    @server.tool(name="asogenic_get_screenshot_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_screenshot_status")
    def asogenic_get_screenshot_status(screenshot_id: str) -> dict[str, Any]:
        """Apple-side asset processing state for one uploaded screenshot. A screenshot can commit
        successfully through asogenic_upload_screenshot and still be permanently rejected afterward
        (e.g. an alpha channel Apple doesn't allow) — this is the only way to see that before a
        submission attempt discovers it. Poll this yourself if asogenic_upload_screenshot's own
        return already moved on with a non-terminal state.

        Returns `{"state": str | None, "errors": list, "warnings": list}`. `state` is one of Apple's
        own values (`"COMPLETE"`, `"FAILED"`, `"IN_PROGRESS"`, etc.) or `None` if not yet known.
        `errors` is non-empty only when `state == "FAILED"` — inspect it for the specific rejection
        reason (e.g. `IMAGE_ALPHA_NOT_ALLOWED`)."""
        return screenshots.get_screenshot_status(screenshot_id=screenshot_id)

    @server.tool(
        name="asogenic_upload_screenshot",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    @log_tool_call("asogenic_upload_screenshot")
    def asogenic_upload_screenshot(
        app_id: str,
        platform: str,
        locale: str,
        display_type: str,
        file_name: str,
        image_base64: str | None = None,
        image_path: str | None = None,
    ) -> dict[str, Any]:
        """Upload one screenshot from exactly one of ``image_base64`` or ``image_path``.
        ``image_path`` avoids putting large media into JSON in stdio/local deployments. Hosted
        callers must use Base64 within the request limit; a staged-upload session is not available.
        `display_type` is passed through as Apple's current device-size id;
        the server intentionally does not freeze the API's evolving enum in code.

        Returns `{screenshot_set_id, screenshot_id, display_type, asset_delivery_state, note?}`.
        `asset_delivery_state` reflects a short bounded poll after upload (a few seconds) — check it:
        `"COMPLETE"` means Apple accepted the image; anything else and a `note` explains to poll
        asogenic_get_screenshot_status yourself, since Apple's processing can outlast this call's
        wait window. Raises immediately (not silently) if the state comes back `"FAILED"` within that
        window, with Apple's own rejection reason (e.g. `IMAGE_ALPHA_NOT_ALLOWED`) in the error."""
        return screenshots.upload_screenshot(
            app_id=app_id,
            platform=platform,
            locale=locale,
            display_type=display_type,
            file_name=file_name,
            image_base64=image_base64,
            image_path=image_path,
        )

    @server.tool(
        name="asogenic_delete_screenshot",
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    @log_tool_call("asogenic_delete_screenshot")
    def asogenic_delete_screenshot(screenshot_id: str) -> dict[str, Any]:
        """Remove one uploaded screenshot by its id (from asogenic_list_screenshots) — use this to
        clear out a screenshot that failed Apple's asset pipeline (asogenic_get_screenshot_status
        shows `"FAILED"`) before re-uploading a corrected one.

        Returns `{"deleted": True}`."""
        return screenshots.delete_screenshot(screenshot_id=screenshot_id)

    @server.tool(
        name="asogenic_reorder_screenshots",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    @log_tool_call("asogenic_reorder_screenshots")
    def asogenic_reorder_screenshots(
        screenshot_set_id: str, screenshot_ids: list[str]
    ) -> dict[str, Any]:
        """Replace the ordered screenshot list for one device-size set."""
        return screenshots.reorder_screenshots(
            screenshot_set_id=screenshot_set_id, screenshot_ids=screenshot_ids
        )
