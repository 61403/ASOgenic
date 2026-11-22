"""Public setup tools shared by the local and hosted MCP compositions."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.release import info as release_info
from asogenic.tools import readiness

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}
_LOCAL_WRITE = {"readOnlyHint": False, "destructiveHint": False}


def register_setup_catalog(server: FastMCP) -> None:
    """Register build diagnostics and product-context intake."""

    @server.tool(name="asogenic_get_server_info", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_server_info", quota_exempt=True)
    def asogenic_get_server_info() -> dict[str, str]:
        """Return the deployed build and wire-contract versions for client diagnostics."""
        return release_info()

    @server.tool(name="asogenic_intake_product_context", annotations=_LOCAL_WRITE)
    @log_tool_call("asogenic_intake_product_context", quota_exempt=True)
    def asogenic_intake_product_context(
        app_id: str,
        platform: str,
        what_it_does: str,
        target_audience: str = "",
        key_features: list[str] | None = None,
        requires_account: bool = False,
        demo_account_notes: str = "",
        privacy_policy_url: str = "",
        support_url: str = "",
        collects_user_data: bool | None = None,
    ) -> dict[str, Any]:
        """Call this once, early, before generating any metadata or checking readiness — it grounds
        every downstream generation tool and removes one of asogenic_get_release_readiness's warnings
        ("no product context recorded"). `collects_user_data` isn't itself a privacy declaration
        (App Privacy has no API — see the module docstring), it's just enough for readiness to flag
        "you said this collects data but declared nothing" as a real gap instead of a guess.
        Quota-exempt: this is bookkeeping, not an ASO operation.

        Returns `{"session_key": str, "stored": True}`."""
        return readiness.intake_product_context(
            app_id=app_id,
            platform=platform,
            what_it_does=what_it_does,
            target_audience=target_audience,
            key_features=key_features,
            requires_account=requires_account,
            demo_account_notes=demo_account_notes,
            privacy_policy_url=privacy_policy_url,
            support_url=support_url,
            collects_user_data=collects_user_data,
        )
