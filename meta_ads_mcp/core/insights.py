"""Insights and Reporting functionality for Meta Ads API."""

import json
from typing import Optional, Union, Dict, List, Any
from .api import meta_api_tool, make_api_request
from .utils import download_image, try_multiple_download_methods
from .server import mcp_server
from .helpers import dump, error, parse_jsonish
import base64
import datetime


# Prefixes of action_type values that are always redundant duplicates of other
# action types already present in the response.  For every canonical event
# (e.g. "purchase"), the Meta API returns 5-8 variants that carry the exact
# same numeric value:
#   omni_purchase, onsite_web_purchase, onsite_web_app_purchase,
#   web_in_store_purchase, web_app_in_store_purchase,
#   offsite_conversion.fb_pixel_purchase  …
# Removing these cuts each insight row from ~4 KB to ~1 KB without any
# information loss.
_REDUNDANT_ACTION_PREFIXES = (
    "omni_",                       # omnichannel roll-up  (== onsite_web_app_*)
    "onsite_web_app_",             # web+app combined     (== onsite_web_*)
    "onsite_web_",                 # web-only subset      (== canonical + onsite)
    "onsite_app_",                 # app-only subset      (== onsite_conversion.*)
    "web_app_in_store_",           # web+app in-store     (== web_in_store_*)
    "offsite_conversion.fb_pixel_",  # pixel attribution  (== canonical type)
)


# Breakdowns that collide with the default action_breakdowns=[action_type] but
# are real Meta fields (so we explicitly clear action_breakdowns instead of
# dropping the action-typed response fields). For non-DCO ads this returns
# empty rows; for DCO ads it returns the breakdown dimension populated.
_BREAKDOWNS_REQUIRING_EMPTY_ACTION_BREAKDOWNS = frozenset({
    "media_type",
})


def _strip_redundant_actions(row: dict) -> dict:
    """Remove redundant action-type entries from a single insight row."""
    for key in ("actions", "action_values", "cost_per_action_type"):
        items = row.get(key)
        if not isinstance(items, list):
            continue
        row[key] = [
            item for item in items
            if not any(
                item.get("action_type", "").startswith(prefix)
                for prefix in _REDUNDANT_ACTION_PREFIXES
            )
        ]
    return row


@mcp_server.tool()
@meta_api_tool
async def get_insights(object_id: str = "", access_token: Optional[str] = None,
                      time_range: Union[str, Dict[str, str]] = "maximum", breakdown: str = "",
                      level: str = "ad", limit: int = 25, after: str = "",
                      action_attribution_windows: Optional[List[str]] = None,
                      action_breakdowns: Optional[List[str]] = None,
                      compact: bool = False,
                      account_id: str = "", campaign_id: str = "",
                      adset_id: str = "", ad_id: str = "",
                      time_increment: Optional[Union[str, int]] = None,
                      compare_time_range: Optional[Union[str, Dict[str, str]]] = None,
                      use_async: bool = False) -> str:
    """
    Get performance insights for a campaign, ad set, ad or account.

    Args:
        object_id: ID of the campaign, ad set, ad or account. You can also use the alias parameters below.
        account_id: Alias for object_id when querying account-level insights
        campaign_id: Alias for object_id when querying campaign-level insights
        adset_id: Alias for object_id when querying ad-set-level insights
        ad_id: Alias for object_id when querying ad-level insights
        access_token: Meta API access token (optional - will use cached token if not provided)
        time_range: Either a preset time range string or a dictionary with "since" and "until" dates in YYYY-MM-DD format
                   Preset options: today, yesterday, this_month, last_month, this_quarter, maximum, data_maximum, 
                   last_3d, last_7d, last_14d, last_28d, last_30d, last_90d, last_week_mon_sun, 
                   last_week_sun_sat, last_quarter, last_year, this_week_mon_today, this_week_sun_today, this_year
                   Dictionary example: {"since":"2023-01-01","until":"2023-01-31"}
        breakdown: Optional breakdown dimension. Valid values include:
                   Demographic: age, gender, country, region, dma
                   Platform/Device: device_platform, platform_position, publisher_platform, impression_device
                   NOTE: platform_position is a Meta-restricted breakdown — Meta requires it to be paired
                   with publisher_platform (otherwise "(#100) ... (action_type, platform_position) is invalid").
                   When you pass platform_position, this tool auto-adds publisher_platform, and the
                   action-typed fields (actions, action_values, conversions, cost_per_action_type) are
                   returned per placement, so you get leads/CPL/conversions broken down by placement.
                   Creative Assets: ad_format_asset, body_asset, call_to_action_asset, description_asset,
                                  image_asset, link_url_asset, title_asset, video_asset, media_type,
                                  creative_relaxation_asset_type, flexible_format_asset_type,
                                  gen_ai_asset_type
                   NOTE: Asset breakdowns (image_asset, video_asset, etc.) only return data for ads
                   running with Dynamic Creative; for non-DCO ads, expect empty rows.
                   NOTE: media_type collides with the default action_breakdowns=[action_type], so
                   this tool auto-overrides action_breakdowns to [] when you pass media_type.
                   Action-typed metrics (actions, action_values, conversions) are still returned
                   but are no longer sliced by action_type alongside media_type.
                   media_asset_url, media_creator, media_destination_url, media_format,
                   media_origin_url, and media_text_content are NOT supported by Meta's Insights API
                   (Meta returns "(#100) Tried accessing nonexisting field"). Use the asset breakdowns
                   above instead.
                   Campaign/Ad Attributes: breakdown_ad_objective, breakdown_reporting_ad_id, app_id, product_id
                   Catalog/DPA: product_id (per-product performance for Advantage+ catalog / DPA ads)
                   Placement: publisher_platform, platform_position, impression_device
                   Demographics: age, gender, region, country, dma
                   Conversion Tracking: coarse_conversion_value, conversion_destination, standard_event_content_type,
                                       signal_source_bucket, is_conversion_id_modeled, fidelity_type, redownload
                   Time-based: hourly_stats_aggregated_by_advertiser_time_zone, 
                              hourly_stats_aggregated_by_audience_time_zone, frequency_value
                   Extensions/Landing: ad_extension_domain, ad_extension_url, landing_destination, 
                                      mdsa_landing_destination
                   Attribution: sot_attribution_model_type, sot_attribution_window, sot_channel, 
                               sot_event_type, sot_source
                   Mobile/SKAN: skan_campaign_id, skan_conversion_id, skan_version, postback_sequence_index
                   CRM/Business: crm_advertiser_l12_territory_ids, crm_advertiser_subvertical_id,
                                crm_advertiser_vertical_id, crm_ult_advertiser_id, user_persona_id, user_persona_name
                   Advanced: hsid, is_auto_advance, is_rendered_as_delayed_skip_ad, mmm, place_page_id,
                            marketing_messages_btn_name, impression_view_time_advertiser_hour_v2, comscore_market,
                            comscore_market_code
        level: Level of aggregation (ad, adset, campaign, account)
        limit: Maximum number of results to return per page (default: 25, Meta API allows much higher values)
        after: Pagination cursor to get the next set of results. Use the 'after' cursor from previous response's paging.next field.
        action_attribution_windows: Optional list of attribution windows (e.g., ["1d_click", "7d_click", "1d_view"]).
                   When specified, actions include additional fields for each window. The 'value' field always shows 7d_click.
        action_breakdowns: Optional list of action_breakdowns to apply to action-typed metrics. Pass [] to disable
                   the default action_type slicing (required when combining action data with breakdowns that collide
                   with action_type, e.g. media_type — auto-applied for media_type when not set).
                   Meta supports values like action_type, action_target_id, action_destination, etc.
        compact: When True, strips redundant action-type duplicates from the response
                 (omni_*, onsite_web_*, offsite_conversion.fb_pixel_*, etc.) to reduce
                 payload size by ~60%. The canonical action types (purchase, add_to_cart,
                 view_content, etc.) are always preserved. Default: False.
        time_increment: Optional daily/monthly split. Integer days (e.g. 1) or 'monthly' / 'all_days'.
        compare_time_range: Optional second range (same shape as time_range). When set, the
                 tool fetches both periods and returns current, comparison, and simple deltas.
        use_async: If True, start an async Insights job instead of waiting inline. Returns a
                 report_run_id; poll with get_insights_job / get_insights_job_results.
                 Preferred for large date ranges or product_id breakdowns.

    Note on response size: This tool always returns a fixed set of fields (impressions, clicks,
    spend, cpc, cpm, ctr, reach, actions, action_values, etc.) and cannot filter to a subset.
    For large result sets (50+ rows), the actions/action_values arrays can make responses very
    large (1–2MB+). If you only need specific metrics like spend or impressions, consider using
    bulk_get_insights with compact=true and the fields parameter:
        bulk_get_insights(level="ad", account_ids=[...], compact=true, fields=["spend", "impressions"])
    bulk_get_insights supports level="ad", "adset", "campaign", and "account".
    """
    # Accept common aliases for object_id (LLMs frequently use these instead)
    if not object_id:
        object_id = account_id or campaign_id or adset_id or ad_id

    if not object_id:
        return json.dumps({"error": "No object ID provided. Use object_id, account_id, campaign_id, adset_id, or ad_id."}, indent=2)
        
    endpoint = f"{object_id}/insights"
    fields = [
        "account_id", "account_name", "campaign_id", "campaign_name",
        "adset_id", "adset_name", "ad_id", "ad_name",
        "impressions", "clicks", "spend", "cpc", "cpm", "ctr", "reach",
        "frequency", "actions", "action_values", "conversions",
        "unique_clicks", "cost_per_action_type",
    ]

    # Meta rejects platform_position on its own: it must be paired with
    # publisher_platform, otherwise "(#100) Current combination of data breakdown
    # columns (action_type, platform_position) is invalid". Auto-add
    # publisher_platform so the request succeeds with placement-level metrics —
    # including the action-typed fields (actions, cost_per_action_type, etc.),
    # which Meta returns per placement once the pairing is in place.
    breakdown_values = [b.strip() for b in breakdown.split(",") if b.strip()] if breakdown else []
    breakdown_set = set(breakdown_values)
    if "platform_position" in breakdown_set and "publisher_platform" not in breakdown_set:
        breakdown_values = ["publisher_platform", *breakdown_values]
        breakdown_set.add("publisher_platform")
    # media_type collides with action_breakdowns=[action_type] but is a real
    # field — override action_breakdowns to empty so the request succeeds with
    # the action-typed metrics intact.
    override_action_breakdowns_empty = bool(
        breakdown_set & _BREAKDOWNS_REQUIRING_EMPTY_ACTION_BREAKDOWNS
    )

    params = _build_insights_params(
        fields=fields,
        level=level,
        limit=limit,
        time_range=time_range,
        breakdown_values=breakdown_values,
        action_breakdowns=action_breakdowns,
        override_action_breakdowns_empty=override_action_breakdowns_empty,
        after=after,
        action_attribution_windows=action_attribution_windows,
        time_increment=time_increment,
    )
    if isinstance(params, str):
        return params  # validation error JSON

    if use_async:
        job = await make_api_request(
            endpoint, access_token, params, method="POST", mutation=False
        )
        if isinstance(job, dict):
            job["note"] = (
                "Async insights job started. Poll get_insights_job with report_run_id, "
                "then get_insights_job_results when async_status is Job Completed."
            )
        return json.dumps(job, indent=2)

    data = await make_api_request(endpoint, access_token, params)

    # In compact mode, strip redundant action-type duplicates to reduce response size.
    if compact and isinstance(data, dict):
        for row in data.get("data", []):
            if isinstance(row, dict):
                _strip_redundant_actions(row)

    if compare_time_range:
        compare_params = _build_insights_params(
            fields=fields,
            level=level,
            limit=limit,
            time_range=compare_time_range,
            breakdown_values=breakdown_values,
            action_breakdowns=action_breakdowns,
            override_action_breakdowns_empty=override_action_breakdowns_empty,
            after="",
            action_attribution_windows=action_attribution_windows,
            time_increment=time_increment,
        )
        if isinstance(compare_params, str):
            return compare_params
        comparison = await make_api_request(endpoint, access_token, compare_params)
        if compact and isinstance(comparison, dict):
            for row in comparison.get("data", []):
                if isinstance(row, dict):
                    _strip_redundant_actions(row)
        return dump({
            "current": data,
            "comparison": comparison,
            "deltas": _summarize_insight_deltas(data, comparison),
        })

    return dump(data)


def _apply_time_range(params: Dict[str, Any], time_range: Union[str, Dict[str, str]]) -> Optional[str]:
    time_range = parse_jsonish(time_range)
    if isinstance(time_range, dict):
        if "since" in time_range and "until" in time_range:
            params["time_range"] = json.dumps(time_range)
            return None
        return json.dumps({
            "error": "Custom time_range must contain both 'since' and 'until' keys in YYYY-MM-DD format"
        }, indent=2)
    params["date_preset"] = time_range
    return None


def _build_insights_params(
    fields: List[str],
    level: str,
    limit: int,
    time_range: Union[str, Dict[str, str]],
    breakdown_values: List[str],
    action_breakdowns: Optional[List[str]],
    override_action_breakdowns_empty: bool,
    after: str,
    action_attribution_windows: Optional[List[str]],
    time_increment: Optional[Union[str, int]],
) -> Union[Dict[str, Any], str]:
    params: Dict[str, Any] = {
        "fields": ",".join(fields),
        "level": level,
        "limit": limit,
    }
    err = _apply_time_range(params, time_range)
    if err:
        return err
    if breakdown_values:
        params["breakdowns"] = ",".join(breakdown_values)
    if action_breakdowns is not None:
        params["action_breakdowns"] = (
            "[" + ",".join(action_breakdowns) + "]" if action_breakdowns else "[]"
        )
    elif override_action_breakdowns_empty:
        params["action_breakdowns"] = "[]"
    if after:
        params["after"] = after
    if action_attribution_windows:
        params["action_attribution_windows"] = "[" + ",".join(f"'{w}'" for w in action_attribution_windows) + "]"
    if time_increment is not None and time_increment != "":
        params["time_increment"] = time_increment
    return params


def _metric_sum(payload: Any, key: str) -> float:
    total = 0.0
    if not isinstance(payload, dict):
        return total
    for row in payload.get("data", []) or []:
        if not isinstance(row, dict):
            continue
        try:
            total += float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _summarize_insight_deltas(current: Any, comparison: Any) -> Dict[str, Any]:
    keys = ("spend", "impressions", "clicks", "reach")
    deltas: Dict[str, Any] = {}
    for key in keys:
        cur = _metric_sum(current, key)
        prev = _metric_sum(comparison, key)
        delta = cur - prev
        pct = (delta / prev * 100.0) if prev else None
        deltas[key] = {
            "current": cur,
            "comparison": prev,
            "delta": delta,
            "delta_pct": round(pct, 2) if pct is not None else None,
        }
    return deltas


_INSIGHT_JOB_FIELDS = (
    "id,async_status,async_percent_completion,is_running,time_ref,"
    "time_completed,date_start,date_stop,account_id,friendly_name"
)


@mcp_server.tool()
@meta_api_tool
async def create_insights_job(
    object_id: str = "",
    access_token: Optional[str] = None,
    time_range: Union[str, Dict[str, str]] = "last_30d",
    breakdown: str = "",
    level: str = "ad",
    limit: int = 500,
    action_attribution_windows: Optional[List[str]] = None,
    action_breakdowns: Optional[List[str]] = None,
    time_increment: Optional[Union[str, int]] = None,
    account_id: str = "",
    campaign_id: str = "",
    adset_id: str = "",
    ad_id: str = "",
) -> str:
    """Start an async Insights report job for large date ranges or heavy breakdowns.

    Returns report_run_id. Poll get_insights_job until async_status is
    "Job Completed", then call get_insights_job_results.

    Args:
        object_id: Campaign, ad set, ad, or account ID
        access_token: Meta API access token (optional)
        time_range: Preset or {since, until}
        breakdown: Optional breakdown (product_id, age, gender, region, publisher_platform, ...)
        level: ad, adset, campaign, account
        limit: Page size for the eventual result
        action_attribution_windows: Attribution windows
        action_breakdowns: Action breakdowns
        time_increment: 1 for daily rows, 'monthly', or 'all_days'
        account_id, campaign_id, adset_id, ad_id: Aliases for object_id
    """
    if not object_id:
        object_id = account_id or campaign_id or adset_id or ad_id
    if not object_id:
        return error("No object ID provided")

    breakdown_values = [b.strip() for b in breakdown.split(",") if b.strip()] if breakdown else []
    breakdown_set = set(breakdown_values)
    if "platform_position" in breakdown_set and "publisher_platform" not in breakdown_set:
        breakdown_values = ["publisher_platform", *breakdown_values]
        breakdown_set.add("publisher_platform")
    override_action_breakdowns_empty = bool(
        breakdown_set & _BREAKDOWNS_REQUIRING_EMPTY_ACTION_BREAKDOWNS
    )
    fields = [
        "account_id", "account_name", "campaign_id", "campaign_name",
        "adset_id", "adset_name", "ad_id", "ad_name",
        "impressions", "clicks", "spend", "cpc", "cpm", "ctr", "reach",
        "frequency", "actions", "action_values", "conversions",
        "unique_clicks", "cost_per_action_type",
    ]
    params = _build_insights_params(
        fields=fields,
        level=level,
        limit=limit,
        time_range=time_range,
        breakdown_values=breakdown_values,
        action_breakdowns=action_breakdowns,
        override_action_breakdowns_empty=override_action_breakdowns_empty,
        after="",
        action_attribution_windows=action_attribution_windows,
        time_increment=time_increment,
    )
    if isinstance(params, str):
        return params
    data = await make_api_request(
        f"{object_id}/insights", access_token, params, method="POST", mutation=False
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_insights_job(
    report_run_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get the status of an async Insights report job.

    Args:
        report_run_id: ID returned by create_insights_job / get_insights(use_async=true)
        access_token: Meta API access token (optional)
    """
    if not report_run_id:
        return error("No report_run_id provided")
    data = await make_api_request(
        report_run_id, access_token, {"fields": _INSIGHT_JOB_FIELDS}
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_insights_job_results(
    report_run_id: str,
    access_token: Optional[str] = None,
    limit: int = 500,
    after: str = "",
    compact: bool = False,
) -> str:
    """Fetch results of a completed async Insights report job.

    Args:
        report_run_id: Completed report run ID
        access_token: Meta API access token (optional)
        limit: Page size
        after: Pagination cursor
        compact: Strip redundant action-type duplicates
    """
    if not report_run_id:
        return error("No report_run_id provided")
    params: Dict[str, Any] = {"limit": limit}
    if after:
        params["after"] = after
    data = await make_api_request(f"{report_run_id}/insights", access_token, params)
    if compact and isinstance(data, dict):
        for row in data.get("data", []):
            if isinstance(row, dict):
                _strip_redundant_actions(row)
    return dump(data)





 