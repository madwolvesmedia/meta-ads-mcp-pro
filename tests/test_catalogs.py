"""Offline tests for catalog, product set, feed, and diagnostics tools."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from meta_ads_mcp.core.catalogs import (
    list_business_catalogs,
    get_catalog,
    get_ad_account_catalogs,
    get_pixel_catalogs,
    list_product_sets,
    create_product_set,
    update_product_set,
    preview_product_set,
    list_catalog_products,
    list_product_feeds,
    get_feed_upload_status,
    get_catalog_diagnostics,
)


def parse(result):
    data = json.loads(result)
    if "data" in data and isinstance(data["data"], str):
        return json.loads(data["data"])
    return data


@pytest.mark.asyncio
async def test_list_business_catalogs_with_business_id():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "cat_1", "name": "Shop"}]}
        result = parse(await list_business_catalogs(business_id="biz_1", access_token="tok"))
        assert result["data"][0]["id"] == "cat_1"
        mock_api.assert_called_once()
        assert mock_api.call_args[0][0] == "biz_1/owned_product_catalogs"


@pytest.mark.asyncio
async def test_get_catalog():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cat_1", "name": "Shop", "product_count": 12}
        result = parse(await get_catalog(catalog_id="cat_1", access_token="tok"))
        assert result["product_count"] == 12


@pytest.mark.asyncio
async def test_get_ad_account_catalogs_queries_assigned_and_business():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.side_effect = [
            {"data": [{"id": "cat_assigned"}]},
            {"id": "act_1", "business": {"id": "biz", "owned_product_catalogs": {"data": [{"id": "cat_owned"}]}}},
        ]
        result = parse(await get_ad_account_catalogs(account_id="123", access_token="tok"))
        assert result["account_id"] == "act_123"
        assert result["assigned_product_catalogs"]["data"][0]["id"] == "cat_assigned"
        endpoints = [c[0][0] for c in mock_api.call_args_list]
        assert "act_123/assigned_product_catalogs" in endpoints


@pytest.mark.asyncio
async def test_get_pixel_catalogs_filters_event_sources():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.side_effect = [
            {"id": "px_1", "owner_business": {"id": "biz_9"}},
            {
                "data": [
                    {"id": "cat_match", "external_event_sources": {"data": [{"id": "px_1"}]}},
                    {"id": "cat_other", "external_event_sources": {"data": [{"id": "px_other"}]}},
                ]
            },
        ]
        result = parse(await get_pixel_catalogs(pixel_id="px_1", access_token="tok"))
        assert [c["id"] for c in result["matching_catalogs"]] == ["cat_match"]


@pytest.mark.asyncio
async def test_create_product_set_sends_filter_json():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "ps_1"}
        result = parse(await create_product_set(
            catalog_id="cat_1",
            name="Shoes",
            filter_rules={"product_type": {"eq": "Shoes"}},
            access_token="tok",
        ))
        assert result["id"] == "ps_1"
        params = mock_api.call_args[0][2]
        assert params["name"] == "Shoes"
        assert "Shoes" in params["filter"]
        assert mock_api.call_args.kwargs.get("method") == "POST"


@pytest.mark.asyncio
async def test_create_product_set_accepts_filter_string():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "ps_2"}
        await create_product_set(
            catalog_id="cat_1",
            name="Sale",
            filter_rules='{"custom_label_0": {"eq": "sale"}}',
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert "sale" in params["filter"]


@pytest.mark.asyncio
async def test_update_product_set():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"success": True}
        await update_product_set(product_set_id="ps_1", name="New", access_token="tok")
        assert mock_api.call_args[0][0] == "ps_1"
        assert mock_api.call_args[0][2]["name"] == "New"


@pytest.mark.asyncio
async def test_preview_product_set_by_id():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.side_effect = [
            {"id": "ps_1", "product_count": 5, "filter": {"brand": {"eq": "Acme"}}},
            {"data": [{"id": "p1", "name": "Widget"}]},
        ]
        result = parse(await preview_product_set(product_set_id="ps_1", access_token="tok"))
        assert result["product_count"] == 5
        assert result["sample_products"]["data"][0]["name"] == "Widget"


@pytest.mark.asyncio
async def test_list_catalog_products_retailer_filter():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": []}
        await list_catalog_products(catalog_id="cat_1", retailer_id="SKU-9", access_token="tok")
        params = mock_api.call_args[0][2]
        assert "SKU-9" in params["filter"]


@pytest.mark.asyncio
async def test_list_product_feeds_and_uploads():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "feed_1"}]}
        feeds = parse(await list_product_feeds(catalog_id="cat_1", access_token="tok"))
        assert feeds["data"][0]["id"] == "feed_1"
        mock_api.return_value = {"data": [{"id": "up_1", "error_count": 0}]}
        uploads = parse(await get_feed_upload_status(feed_id="feed_1", access_token="tok"))
        assert uploads["data"][0]["id"] == "up_1"


@pytest.mark.asyncio
async def test_get_catalog_diagnostics_combines_edges():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.side_effect = [
            {"id": "cat_1", "product_count": 10},
            {"data": [{"type": "missing_image"}]},
            {"data": []},
        ]
        result = parse(await get_catalog_diagnostics(catalog_id="cat_1", access_token="tok"))
        assert result["catalog"]["id"] == "cat_1"
        assert result["diagnostics"]["data"][0]["type"] == "missing_image"
        endpoints = [c[0][0] for c in mock_api.call_args_list]
        assert "cat_1/diagnostics" in endpoints
        assert "cat_1/event_stats" in endpoints


@pytest.mark.asyncio
async def test_list_product_sets():
    with patch("meta_ads_mcp.core.catalogs.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "ps_1"}]}
        result = parse(await list_product_sets(catalog_id="cat_1", access_token="tok"))
        assert result["data"][0]["id"] == "ps_1"
