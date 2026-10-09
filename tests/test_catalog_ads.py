"""Tests for catalog creatives and catalog ad sets, plus create_adset catalog params."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from meta_ads_mcp.core.catalog_ads import create_catalog_ad_creative, create_catalog_adset
from meta_ads_mcp.core.adsets import create_adset, update_adset
from meta_ads_mcp.core.campaigns import create_campaign


def parse(result):
    data = json.loads(result)
    if "data" in data and isinstance(data["data"], str):
        return json.loads(data["data"])
    return data


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_carousel_template():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_1"}
        result = parse(await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            message="Buy {{product.name}}",
            headline="{{product.name}}",
            description="{{product.price}}",
            call_to_action_type="SHOP_NOW",
            url_tags="utm_source=facebook",
            access_token="tok",
        ))
        assert result["id"] == "cr_1"
        assert result["product_set_id"] == "ps_1"
        params = mock_api.call_args[0][2]
        assert params["product_set_id"] == "ps_1"
        story = params["object_story_spec"]
        assert story["page_id"] == "page_1"
        assert story["template_data"]["call_to_action"]["type"] == "SHOP_NOW"
        assert "{{product.name}}" in story["template_data"]["message"]
        assert story["template_data"]["name"] == "{{product.name}}"
        assert story["template_data"]["description"] == "{{product.price}}"
        assert params["url_tags"] == "utm_source=facebook"
        assert "force_single_link" not in story["template_data"]
        assert "asset_feed_spec" not in params


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_single_and_collection():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_single"}
        await create_catalog_ad_creative(
            account_id="1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            format="single",
            access_token="tok",
        )
        story = mock_api.call_args[0][2]["object_story_spec"]
        assert story["template_data"]["force_single_link"] is True

        mock_api.return_value = {"id": "cr_col"}
        await create_catalog_ad_creative(
            account_id="1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            format="collection",
            collection_hero_image_hash="abc",
            enable_dynamic_media=True,
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        feed = params["asset_feed_spec"]
        assert feed["ad_formats"] == ["COLLECTION"]
        assert feed["images"][0]["hash"] == "abc"
        assert feed["descriptions"] == [{"text": "{{product.price}}"}]
        assert feed["titles"] == [{"text": "{{product.name}}"}]
        assert "degrees_of_freedom_spec" in params


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_format_automation_and_enhancements():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_auto"}
        result = parse(await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            message="Buy {{product.name}}",
            headline="{{product.name}}",
            description="{{product.price}}",
            format="auto",
            enable_enhancements=True,
            access_token="tok",
        ))
        assert result["format"] == "auto"
        params = mock_api.call_args[0][2]
        story = params["object_story_spec"]["template_data"]
        assert story["name"] == "{{product.name}}"
        assert story["description"] == "{{product.price}}"
        assert story["message"] == "Buy {{product.name}}"
        feed = params["asset_feed_spec"]
        assert feed["ad_formats"] == ["CAROUSEL", "COLLECTION"]
        assert feed["optimization_type"] == "REGULAR"
        assert feed["titles"] == [{"text": "{{product.name}}"}]
        assert feed["descriptions"] == [{"text": "{{product.price}}"}]
        assert feed["bodies"] == [{"text": "Buy {{product.name}}"}]
        assert feed["link_urls"] == [{"website_url": "https://shop.example.com"}]
        assert feed["call_to_action_types"] == ["SHOP_NOW"]
        features = params["degrees_of_freedom_spec"]["creative_features_spec"]
        assert "standard_enhancements" not in features
        assert features["standard_enhancements_catalog"]["enroll_status"] == "OPT_IN"
        assert features["image_enhancement"]["enroll_status"] == "OPT_IN"
        assert features["enhance_cta"]["enroll_status"] == "OPT_IN"


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_asset_feed_spec_passthrough():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_feed"}
        await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            format="carousel",
            asset_feed_spec={"ad_formats": ["CAROUSEL", "COLLECTION"], "optimization_type": "REGULAR"},
            degrees_of_freedom_spec={
                "creative_features_spec": {
                    "standard_enhancements": {"enroll_status": "OPT_IN"},
                }
            },
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        feed = params["asset_feed_spec"]
        assert feed["ad_formats"] == ["CAROUSEL", "COLLECTION"]
        assert feed["descriptions"] == [{"text": "{{product.price}}"}]
        assert feed["titles"] == [{"text": "{{product.name}}"}]
        assert params["degrees_of_freedom_spec"]["creative_features_spec"]["standard_enhancements"]["enroll_status"] == "OPT_IN"


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_enhancements_default_off():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_plain"}
        await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            format="auto",
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert "degrees_of_freedom_spec" not in params
        assert params["asset_feed_spec"]["descriptions"] == [{"text": "{{product.price}}"}]


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_standard_enhancements_catalog_flag():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_sec"}
        await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            standard_enhancements_catalog=True,
            access_token="tok",
        )
        features = mock_api.call_args[0][2]["degrees_of_freedom_spec"]["creative_features_spec"]
        assert features == {"standard_enhancements_catalog": {"enroll_status": "OPT_IN"}}

        mock_api.return_value = {"id": "cr_sec_off"}
        await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            enable_enhancements=True,
            standard_enhancements_catalog=False,
            access_token="tok",
        )
        features = mock_api.call_args[0][2]["degrees_of_freedom_spec"]["creative_features_spec"]
        assert features["standard_enhancements_catalog"]["enroll_status"] == "OPT_OUT"
        assert features["image_uncrop"]["enroll_status"] == "OPT_IN"


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_template_data_fills_both_shapes():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "cr_td"}
        await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://shop.example.com",
            format="auto",
            template_data={
                "name": "{{product.brand}}",
                "description": "{{product.current_price}}",
                "message": "Now {{product.name}}",
            },
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        td = params["object_story_spec"]["template_data"]
        assert td["name"] == "{{product.brand}}"
        assert td["description"] == "{{product.current_price}}"
        assert td["message"] == "Now {{product.name}}"
        feed = params["asset_feed_spec"]
        assert feed["titles"] == [{"text": "{{product.brand}}"}]
        assert feed["descriptions"] == [{"text": "{{product.current_price}}"}]
        assert feed["bodies"] == [{"text": "Now {{product.name}}"}]


@pytest.mark.asyncio
async def test_create_catalog_adset_cbo_sales_defaults():
    with patch("meta_ads_mcp.core.catalog_ads.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "adset_1"}
        result = parse(await create_catalog_adset(
            account_id="act_1",
            campaign_id="camp_1",
            name="Shoes DPA",
            product_set_id="ps_shoes",
            pixel_id="px_1",
            excluded_custom_audience_ids=["aud_buyers"],
            access_token="tok",
        ))
        assert result["id"] == "adset_1"
        params = mock_api.call_args[0][2]
        assert params["status"] == "PAUSED"
        assert params["optimization_goal"] == "VALUE"
        assert params["promoted_object"]["product_set_id"] == "ps_shoes"
        assert params["promoted_object"]["custom_event_type"] == "PURCHASE"
        assert params["promoted_object"]["pixel_id"] == "px_1"
        assert params["targeting"]["targeting_automation"]["advantage_audience"] == 1
        assert params["targeting"]["excluded_custom_audiences"][0]["id"] == "aud_buyers"
        assert "daily_budget" not in params  # CBO-friendly


@pytest.mark.asyncio
async def test_create_adset_catalog_convenience_params():
    with patch("meta_ads_mcp.core.adsets.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "adset_2", "bid_strategy": "LOWEST_COST_WITHOUT_CAP"}
        # First call may be the CBO preflight GET on the campaign.
        mock_api.side_effect = [
            {"id": "camp_1", "name": "Sales", "bid_strategy": "LOWEST_COST_WITHOUT_CAP"},
            {"id": "adset_2"},
        ]
        await create_adset(
            account_id="act_1",
            campaign_id="camp_1",
            name="DPA set",
            optimization_goal="VALUE",
            billing_event="IMPRESSIONS",
            product_set_id="ps_1",
            custom_event_type="PURCHASE",
            pixel_id="px_1",
            excluded_custom_audience_ids=["aud_1"],
            advantage_audience=True,
            access_token="tok",
        )
        create_call = mock_api.call_args_list[-1]
        params = create_call[0][2]
        promo = json.loads(params["promoted_object"]) if isinstance(params["promoted_object"], str) else params["promoted_object"]
        assert promo["product_set_id"] == "ps_1"
        assert promo["custom_event_type"] == "PURCHASE"
        targeting = json.loads(params["targeting"]) if isinstance(params["targeting"], str) else params["targeting"]
        assert targeting["excluded_custom_audiences"][0]["id"] == "aud_1"
        assert targeting["targeting_automation"]["advantage_audience"] == 1


@pytest.mark.asyncio
async def test_update_adset_promoted_object():
    with patch("meta_ads_mcp.core.adsets.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"success": True}
        await update_adset(
            adset_id="adset_9",
            product_set_id="ps_new",
            custom_event_type="ADD_TO_CART",
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        promo = json.loads(params["promoted_object"]) if isinstance(params["promoted_object"], str) else params["promoted_object"]
        assert promo["product_set_id"] == "ps_new"
        assert promo["custom_event_type"] == "ADD_TO_CART"


@pytest.mark.asyncio
async def test_create_campaign_promoted_object_and_paused_default():
    with patch("meta_ads_mcp.core.campaigns.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "camp_9"}
        await create_campaign(
            account_id="act_1",
            name="Catalog CBO",
            objective="OUTCOME_SALES",
            daily_budget=20000,
            special_ad_categories=[],
            promoted_object={"product_catalog_id": "cat_1"},
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["status"] == "PAUSED"
        assert params["objective"] == "OUTCOME_SALES"
        promo = json.loads(params["promoted_object"]) if isinstance(params["promoted_object"], str) else params["promoted_object"]
        assert promo["product_catalog_id"] == "cat_1"
