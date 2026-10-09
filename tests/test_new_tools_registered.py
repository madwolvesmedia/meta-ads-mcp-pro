"""Ensure new P0/P1 tools are registered and deep-research fetch/search stay gone."""

import pytest


@pytest.mark.asyncio
async def test_catalog_and_safety_tools_are_registered():
    from meta_ads_mcp.core.server import mcp_server

    tool_names = {tool.name for tool in await mcp_server.list_tools()}

    expected = {
        "list_business_catalogs",
        "get_catalog",
        "get_ad_account_catalogs",
        "get_pixel_catalogs",
        "list_product_sets",
        "create_product_set",
        "create_catalog_ad_creative",
        "create_catalog_adset",
        "list_custom_audiences",
        "create_lookalike_audience",
        "create_product_audience",
        "upload_custom_audience_users",
        "list_pixels",
        "create_custom_conversion",
        "delete_campaign",
        "copy_campaign",
        "bulk_update_status",
        "create_insights_job",
        "graph_api_get",
        "list_ad_rules",
        "upload_ad_video",
        "list_instagram_accounts",
        "list_lead_forms",
        "get_account_funding",
        "generate_ad_preview",
        "list_budget_schedules",
    }
    missing = expected - tool_names
    assert not missing, f"missing tools: {sorted(missing)}"
    assert "fetch" not in tool_names
    assert "search" not in tool_names
    # Existing tools keep their names
    assert "create_campaign" in tool_names
    assert "create_adset" in tool_names
    assert "get_insights" in tool_names
