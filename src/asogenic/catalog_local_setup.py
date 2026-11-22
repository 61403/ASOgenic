"""Public local connection, discovery, and workspace setup tools."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import auth_discovery

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_local_setup_catalog(server: FastMCP) -> None:
    """Register local-safe setup tools without hosted endpoints or administration."""

    @server.tool(name="asogenic_list_capabilities", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_capabilities", quota_exempt=True)
    def asogenic_list_capabilities() -> dict[str, Any]:
        """Return the local MCP capability groups and their first tools to call.

        This is a discovery aid, not a credential check. Use asogenic_auth_status to check
        App Store Connect credentials configured for this process.
        """
        return {
            "server": {"name": "ASOgenic", "mode": "local stdio"},
            "capabilities": [
                {
                    "id": "auth_and_discovery",
                    "entry_tools": ["asogenic_auth_status", "asogenic_list_apps", "asogenic_resolve_app"],
                },
                {
                    "id": "release_management",
                    "entry_tools": ["asogenic_get_release_readiness", "asogenic_fetch_source", "asogenic_submit_for_review"],
                },
                {
                    "id": "aso",
                    "entry_tools": ["asogenic_research_keywords", "asogenic_assemble_locale"],
                },
                {
                    "id": "product_pages_and_media",
                    "entry_tools": ["asogenic_list_custom_product_pages", "asogenic_list_screenshots"],
                },
                {
                    "id": "testflight_and_reviews",
                    "entry_tools": ["asogenic_list_beta_groups", "asogenic_list_customer_reviews"],
                },
                {
                    "id": "monetization",
                    "entry_tools": ["asogenic_get_setup_capabilities", "asogenic_list_subscriptions"],
                },
            ],
            "status_checks": {
                "tool_inventory": "This catalog is available without ASC credentials.",
                "asc_credentials": {"tool": "asogenic_auth_status"},
            },
            "manual_prerequisites": [
                "Create the app record in App Store Connect; Apple does not expose app-record creation through its API.",
                "App Privacy and some subscription setup tasks require supported App Store Connect or RevenueCat UI workflows.",
            ],
        }

    @server.tool(name="asogenic_auth_status", annotations=_READ_ONLY)
    @log_tool_call("asogenic_auth_status", quota_exempt=True)
    def asogenic_auth_status() -> dict[str, Any]:
        """Check whether this process's local ASC_KEY_ID, ASC_ISSUER_ID, and ASC_P8_PATH work.

        Reports only local credential state; private-key material is never accepted or returned.
        """
        return auth_discovery.auth_status()
