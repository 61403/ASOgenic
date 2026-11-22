"""Standalone public local stdio MCP composition."""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from asogenic.catalog_local_setup import register_local_setup_catalog
from asogenic.catalog_media import register_media_catalog
from asogenic.catalog_metadata import register_metadata_catalog
from asogenic.catalog_post_launch import register_post_launch_catalog
from asogenic.catalog_release import register_release_catalog
from asogenic.catalog_setup import register_setup_catalog
from asogenic.context import LocalRuntime, configure_runtime
from asogenic.core_catalog import register_aso_catalog
from asogenic.release import info as release_info

_LOCAL_INSTRUCTIONS = (
    "ASOgenic is a local App Store Connect release-management MCP server. "
    "Configure ASC_KEY_ID, ASC_ISSUER_ID, and ASC_P8_PATH in the environment. "
    "Use asogenic_auth_status to check the local connection."
)


async def create_local_mcp() -> FastMCP:
    """Compose the complete local catalog directly from public builders."""
    configure_runtime(LocalRuntime())
    server = FastMCP("asogenic", instructions=_LOCAL_INSTRUCTIONS, version=release_info()["server_version"])
    register_local_setup_catalog(server)
    register_setup_catalog(server)
    register_aso_catalog(server)
    register_metadata_catalog(server)
    register_release_catalog(server)
    register_media_catalog(server)
    register_post_launch_catalog(server)
    return server


def main() -> None:
    """Run the standalone stdio server without importing the hosted adapter."""
    LocalRuntime().ensure_pool_signing_secret()
    asyncio.run(create_local_mcp()).run()


if __name__ == "__main__":
    main()
