"""Tests for native copies, bulk status, audiences, pixels, and extras."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from meta_ads_mcp.core.lifecycle import copy_campaign, copy_adset, copy_ad, bulk_update_status, archive_campaign
from meta_ads_mcp.core.audiences import (
    create_custom_audience,
    create_lookalike_audience,
    create_product_audience,
    upload_custom_audience_users,
    hash_customer_rows,
    list_saved_audiences,
)
from meta_ads_mcp.core.pixels import list_pixels, create_custom_conversion, get_pixel_stats
from meta_ads_mcp.core.extras import graph_api_get, upload_ad_video, generate_ad_preview, list_ad_rules
from meta_ads_mcp.core.insights import create_insights_job, get_insights, get_insights_job_results
from meta_ads_mcp.core.budget_schedules import list_budget_schedules, delete_budget_schedule


def parse(result):
    data = json.loads(result)
    if "data" in data and isinstance(data["data"], str):
        return json.loads(data["data"])
    return data


@pytest.mark.asyncio
async def test_copy_campaign_uses_graph_copies_paused():
    with patch("meta_ads_mcp.core.lifecycle.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"copied_campaign_id": "c2"}
        result = parse(await copy_campaign(campaign_id="c1", access_token="tok"))
        assert result["copied_campaign_id"] == "c2"
        assert mock_api.call_args[0][0] == "c1/copies"
        params = mock_api.call_args[0][2]
        assert params["status_option"] == "PAUSED"
        assert params["deep_copy"] is True
        assert mock_api.call_args.kwargs.get("method") == "POST"


@pytest.mark.asyncio
async def test_copy_adset_and_ad():
    with patch("meta_ads_mcp.core.lifecycle.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "x"}
        await copy_adset(adset_id="as1", campaign_id="c9", access_token="tok")
        assert mock_api.call_args[0][0] == "as1/copies"
        assert mock_api.call_args[0][2]["campaign_id"] == "c9"
        await copy_ad(ad_id="ad1", adset_id="as9", access_token="tok")
        assert mock_api.call_args[0][0] == "ad1/copies"
        assert mock_api.call_args[0][2]["status_option"] == "PAUSED"


@pytest.mark.asyncio
async def test_archive_campaign():
    with patch("meta_ads_mcp.core.lifecycle.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"success": True}
        await archive_campaign(campaign_id="c1", access_token="tok")
        assert mock_api.call_args[0][2]["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_bulk_update_status_batches():
    with patch("meta_ads_mcp.core.lifecycle.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = [{"code": 200, "body": "{\"success\":true}"}]
        ids = [str(i) for i in range(51)]
        result = parse(await bulk_update_status(object_ids=ids, status="PAUSED", access_token="tok"))
        assert result["count"] == 51
        assert mock_api.call_count == 2
        first_batch = mock_api.call_args_list[0][0][2]["batch"]
        assert len(first_batch) == 50
        assert first_batch[0]["body"] == "status=PAUSED"


@pytest.mark.asyncio
async def test_create_website_audience():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "aud_1"}
        result = parse(await create_custom_audience(
            account_id="act_1",
            name="Visitors 30d",
            subtype="WEBSITE",
            pixel_id="px_1",
            retention_days=30,
            access_token="tok",
        ))
        assert result["id"] == "aud_1"
        params = mock_api.call_args[0][2]
        assert "subtype" not in params
        assert params["pixel_id"] == "px_1"


@pytest.mark.asyncio
async def test_create_website_audience_omits_subtype_even_when_default_custom():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "aud_2"}
        await create_custom_audience(
            account_id="act_1",
            name="Pixel visitors",
            pixel_id="px_1",
            rule={"inclusions": {"operator": "or", "rules": []}},
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert "subtype" not in params
        assert params["pixel_id"] == "px_1"


@pytest.mark.asyncio
async def test_create_customer_list_audience_sends_subtype_custom():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "aud_c"}
        await create_custom_audience(
            account_id="act_1",
            name="CRM list",
            subtype="CUSTOM",
            customer_file_source="USER_PROVIDED_ONLY",
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["subtype"] == "CUSTOM"
        assert params["customer_file_source"] == "USER_PROVIDED_ONLY"


@pytest.mark.asyncio
async def test_create_custom_audience_lookalike_sends_subtype():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "lal_2"}
        await create_custom_audience(
            account_id="act_1",
            name="LAL",
            subtype="LOOKALIKE",
            origin_audience_id="aud_1",
            lookalike_spec={"country": "US", "ratio": 0.01},
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["subtype"] == "LOOKALIKE"


@pytest.mark.asyncio
async def test_create_product_audience_convenience_events():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "pa_1"}
        result = parse(await create_product_audience(
            account_id="act_1",
            name="Viewed not purchased",
            product_set_id="ps_1",
            view_content_days=14,
            add_to_cart_days=14,
            exclude_purchase_days=7,
            access_token="tok",
        ))
        assert result["id"] == "pa_1"
        assert mock_api.call_args[0][0] == "act_1/product_audiences"
        assert mock_api.call_args.kwargs.get("method") == "POST"
        params = mock_api.call_args[0][2]
        assert params["product_set_id"] == "ps_1"
        events = [c["rule"]["event"]["eq"] for c in params["inclusions"]]
        assert events == ["ViewContent", "AddToCart"]
        assert params["inclusions"][0]["retention_seconds"] == 14 * 86400
        assert params["exclusions"][0]["rule"]["event"]["eq"] == "Purchase"
        assert params["exclusions"][0]["retention_seconds"] == 7 * 86400


@pytest.mark.asyncio
async def test_create_product_audience_explicit_clauses():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "pa_2"}
        await create_product_audience(
            account_id="1",
            name="ATC",
            product_set_id="ps_9",
            inclusions=[{"event": "AddToCart", "retention_days": 30}],
            exclusions=[{"retention_seconds": 86400, "rule": {"event": {"eq": "Purchase"}}}],
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["inclusions"][0]["retention_seconds"] == 30 * 86400
        assert params["inclusions"][0]["rule"]["event"]["eq"] == "AddToCart"
        assert params["exclusions"][0]["retention_seconds"] == 86400


@pytest.mark.asyncio
async def test_create_lookalike():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "lal_1"}
        await create_lookalike_audience(
            account_id="1",
            name="LAL 1%",
            origin_audience_id="aud_1",
            country="US",
            ratio=0.01,
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["subtype"] == "LOOKALIKE"
        assert params["lookalike_spec"]["country"] == "US"
        assert params["lookalike_spec"]["ratio"] == 0.01


@pytest.mark.asyncio
async def test_upload_users_requires_confirm_and_hashes():
    result = parse(await upload_custom_audience_users(
        audience_id="aud_1",
        schema=["EMAIL"],
        data=[["ada.lovelace"]],
        access_token="tok",
    ))
    assert result["error"]["code"] == "confirm_required"

    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"num_received": 1}
        result = parse(await upload_custom_audience_users(
            audience_id="aud_1",
            schema=["EMAIL"],
            data=[["ada.lovelace"]],
            confirm=True,
            access_token="tok",
        ))
        payload = mock_api.call_args[0][2]["payload"]
        assert payload["schema"] == ["EMAIL"]
        hashed = payload["data"][0][0]
        assert hashed != "ada.lovelace"
        assert len(hashed) == 64
        assert result["hashed"] is True


def test_hash_customer_rows_dict_input():
    rows = hash_customer_rows(["EMAIL", "FN"], [{"EMAIL": "ada.lovelace", "FN": "Ada"}])
    assert len(rows[0][0]) == 64
    assert len(rows[0][1]) == 64


@pytest.mark.asyncio
async def test_pixels_and_conversions():
    with patch("meta_ads_mcp.core.pixels.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "px_1"}]}
        result = parse(await list_pixels(account_id="act_1", access_token="tok"))
        assert mock_api.call_args[0][0] == "act_1/adspixels"
        mock_api.return_value = {"data": [{"aggregation": "event"}]}
        await get_pixel_stats(pixel_id="px_1", aggregation="event", access_token="tok")
        assert mock_api.call_args[0][0] == "px_1/stats"
        mock_api.return_value = {"id": "cc_1"}
        await create_custom_conversion(
            account_id="act_1",
            name="Thank you",
            pixel_id="px_1",
            custom_event_type="PURCHASE",
            rule={"url": {"i_contains": "/thanks"}},
            access_token="tok",
        )
        params = mock_api.call_args[0][2]
        assert params["event_source_id"] == "px_1"


@pytest.mark.asyncio
async def test_insights_async_job_and_comparison():
    with patch("meta_ads_mcp.core.insights.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"report_run_id": "rr_1"}
        result = parse(await create_insights_job(
            object_id="act_1",
            time_range={"since": "2026-01-01", "until": "2026-01-31"},
            breakdown="product_id",
            access_token="tok",
        ))
        assert result["report_run_id"] == "rr_1"
        assert mock_api.call_args.kwargs.get("method") == "POST"
        assert mock_api.call_args.kwargs.get("mutation") is False
        params = mock_api.call_args[0][2]
        assert "product_id" in params["breakdowns"]

        mock_api.side_effect = [
            {"data": [{"spend": "100", "impressions": "10"}]},
            {"data": [{"spend": "40", "impressions": "8"}]},
        ]
        compared = parse(await get_insights(
            object_id="camp_1",
            time_range={"since": "2026-02-01", "until": "2026-02-07"},
            compare_time_range={"since": "2026-01-25", "until": "2026-01-31"},
            access_token="tok",
        ))
        assert compared["deltas"]["spend"]["current"] == 100
        assert compared["deltas"]["spend"]["comparison"] == 40
        assert compared["deltas"]["spend"]["delta"] == 60


@pytest.mark.asyncio
async def test_get_insights_use_async_posts_job():
    with patch("meta_ads_mcp.core.insights.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"report_run_id": "rr_9"}
        result = parse(await get_insights(
            object_id="act_1",
            use_async=True,
            breakdown="age,gender",
            access_token="tok",
        ))
        assert result["report_run_id"] == "rr_9"
        assert mock_api.call_args.kwargs.get("mutation") is False


@pytest.mark.asyncio
async def test_insights_job_results_compact():
    with patch("meta_ads_mcp.core.insights.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {
            "data": [{"actions": [{"action_type": "purchase", "value": "1"}, {"action_type": "omni_purchase", "value": "1"}]}]
        }
        result = parse(await get_insights_job_results(
            report_run_id="rr_1", compact=True, access_token="tok"
        ))
        types = [a["action_type"] for a in result["data"][0]["actions"]]
        assert "purchase" in types
        assert "omni_purchase" not in types


@pytest.mark.asyncio
async def test_graph_api_get_rejects_urls_and_allows_ids():
    bad = parse(await graph_api_get(object_id="https://evil.example", access_token="tok"))
    assert "Invalid object_id" in bad["error"]
    nested = parse(await graph_api_get(object_id="act_1", edge="foo/bar", access_token="tok"))
    assert "Invalid edge" in nested["error"]
    with patch("meta_ads_mcp.core.extras.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"id": "act_1"}
        await graph_api_get(object_id="act_1", fields="id,name", access_token="tok")
        assert mock_api.call_args[0][0] == "act_1"
        assert mock_api.call_args.kwargs.get("method") == "GET"


@pytest.mark.asyncio
async def test_upload_ad_video_rejects_private_url():
    result = parse(await upload_ad_video(
        account_id="act_1", file_url="http://127.0.0.1/video.mp4", access_token="tok"
    ))
    assert "error" in result


@pytest.mark.asyncio
async def test_generate_ad_preview_and_rules_and_schedules():
    with patch("meta_ads_mcp.core.extras.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"body": "<iframe>"}]}
        await generate_ad_preview(ad_id="ad_1", ad_format="DESKTOP_FEED_STANDARD", access_token="tok")
        assert mock_api.call_args[0][0] == "ad_1/previews"
        mock_api.return_value = {"data": []}
        await list_ad_rules(account_id="act_1", access_token="tok")
        assert mock_api.call_args[0][0] == "act_1/adrules_library"

    with patch("meta_ads_mcp.core.budget_schedules.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "bs_1"}]}
        await list_budget_schedules(campaign_id="c1", access_token="tok")
        assert mock_api.call_args[0][0] == "c1/budget_schedules"
        blocked = parse(await delete_budget_schedule(budget_schedule_id="bs_1", access_token="tok"))
        assert blocked["error"]["code"] == "confirm_required"


@pytest.mark.asyncio
async def test_list_saved_audiences():
    with patch("meta_ads_mcp.core.audiences.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"data": [{"id": "sv_1"}]}
        result = parse(await list_saved_audiences(account_id="act_1", access_token="tok"))
        assert result["data"][0]["id"] == "sv_1"
        assert mock_api.call_args[0][0] == "act_1/saved_audiences"
