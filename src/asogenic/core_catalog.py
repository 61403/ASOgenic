"""Public-safe MCP catalog groups shared by local and hosted servers.

This module contains only core tool registration. Runtime selection, authentication,
tenant management, and HTTP hosting stay in their respective adapters.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from asogenic.logging_config import log_tool_call
from asogenic.tools import aso as aso_tools

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


def register_aso_catalog(server: FastMCP) -> None:
    """Register the shared keyword research and deterministic ASO tools."""

    @server.tool(name="asogenic_research_keywords", annotations=_READ_ONLY)
    @log_tool_call("asogenic_research_keywords")
    async def asogenic_research_keywords(
        bundle_id: str,
        locale: str,
        seeds: list[str],
        target_genre_id: int | None = None,
        brand_name: str | None = None,
        excluded_brand_terms: list[str] | None = None,
        allow_generic_seeds: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Research one locale using Apple autocomplete and iTunes Search, then cache the pool."""
        return await aso_tools.research_keywords(
            bundle_id=bundle_id,
            locale=locale,
            seeds=seeds,
            target_genre_id=target_genre_id,
            brand_name=brand_name,
            excluded_brand_terms=excluded_brand_terms,
            allow_generic_seeds=allow_generic_seeds,
            force_refresh=force_refresh,
        )

    @server.tool(name="asogenic_complete_local_research", annotations=_READ_ONLY)
    @log_tool_call("asogenic_complete_local_research")
    async def asogenic_complete_local_research(
        plan: dict[str, Any], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        """Score raw public Apple research evidence collected locally under an ASOgenic plan."""
        return await aso_tools.complete_local_research(plan=plan, evidence=evidence)

    @server.tool(name="asogenic_resolve_genre", annotations=_READ_ONLY)
    @log_tool_call("asogenic_resolve_genre")
    async def asogenic_resolve_genre(bundle_id: str) -> dict[str, Any]:
        """Return the app's App Store category ID, resolved from its public listing."""
        return await aso_tools.resolve_genre(bundle_id=bundle_id)

    @server.tool(name="asogenic_assemble_locale", annotations=_READ_ONLY)
    @log_tool_call("asogenic_assemble_locale")
    def asogenic_assemble_locale(
        pool: dict[str, Any],
        name_candidates: list[str],
        subtitle_candidates: list[str],
        current_name: str,
        current_subtitle: str,
        current_keywords: str = "",
        category_name: str | None = None,
        brand_name: str | None = None,
        excluded_brand_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Select metadata candidates and pack a byte-exact keyword field from the pool."""
        return aso_tools.assemble_locale(
            pool=pool,
            name_candidates=name_candidates,
            subtitle_candidates=subtitle_candidates,
            current_name=current_name,
            current_subtitle=current_subtitle,
            current_keywords=current_keywords,
            category_name=category_name,
            brand_name=brand_name,
            excluded_brand_terms=excluded_brand_terms,
        )

    @server.tool(name="asogenic_list_brand_candidates", annotations=_READ_ONLY)
    @log_tool_call("asogenic_list_brand_candidates")
    def asogenic_list_brand_candidates(pool: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank competitor apps whose brands appear among the pool's keyword terms."""
        return aso_tools.list_brand_candidates(pool=pool)

    @server.tool(name="asogenic_edit_keyword_field", annotations=_READ_ONLY)
    @log_tool_call("asogenic_edit_keyword_field")
    def asogenic_edit_keyword_field(
        field: str,
        term: str,
        action: str,
        style: str,
        covered_elsewhere: list[str] | None = None,
        is_brand_term: bool = False,
        is_autocomplete_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Insert or remove one term from a keyword field under the assembler's rules."""
        return aso_tools.edit_keyword_field(
            field=field,
            term=term,
            action=action,
            style=style,
            covered_elsewhere=covered_elsewhere,
            is_brand_term=is_brand_term,
            is_autocomplete_confirmed=is_autocomplete_confirmed,
        )

    @server.tool(name="asogenic_optimize_source_search", annotations=_READ_ONLY)
    @log_tool_call("asogenic_optimize_source_search")
    async def asogenic_optimize_source_search(
        bundle_id: str,
        source_locale: str,
        current: dict[str, Any],
        seeds: list[str],
        target_genre_id: int | None = None,
        brand_name: str | None = None,
        excluded_brand_terms: list[str] | None = None,
        force_refresh: bool = False,
        allow_generic_seeds: bool = False,
    ) -> dict[str, Any]:
        """Research and propose search-optimized metadata for the source listing."""
        return await aso_tools.optimize_source_search(
            bundle_id=bundle_id,
            source_locale=source_locale,
            current=current,
            seeds=seeds,
            target_genre_id=target_genre_id,
            brand_name=brand_name,
            excluded_brand_terms=excluded_brand_terms,
            force_refresh=force_refresh,
            allow_generic_seeds=allow_generic_seeds,
        )

    @server.tool(name="asogenic_get_conversion_guidance", annotations=_READ_ONLY)
    @log_tool_call("asogenic_get_conversion_guidance")
    def asogenic_get_conversion_guidance(
        locale: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        """Return barred-content rules and the current description and promotional text."""
        return aso_tools.get_conversion_guidance(locale=locale, content=content)
