"""Pixels / datasets, event stats, and custom conversions."""

from typing import Any, Dict, Optional

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import dump, error, parse_jsonish
from .server import mcp_server

_PIXEL_FIELDS = (
    "id,name,is_unavailable,last_fired_time,creation_time,"
    "owner_ad_account,owner_business,data_use_setting"
)

_CONVERSION_FIELDS = (
    "id,name,custom_event_type,pixel,rule,description,default_conversion_value,"
    "creation_time,is_unavailable,first_fired_time,last_fired_time,aggregation_rule"
)


@mcp_server.tool()
@meta_api_tool
async def list_pixels(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
) -> str:
    """List pixels / datasets for an ad account.

    Args:
        account_id: Ad account ID (act_XXXXXXXXX)
        access_token: Meta API access token (optional)
        limit: Maximum pixels to return
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    data = await make_api_request(
        f"{account_id}/adspixels",
        access_token,
        {"fields": _PIXEL_FIELDS, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_pixel(
    pixel_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a pixel / dataset by ID.

    Args:
        pixel_id: Pixel (dataset) ID
        access_token: Meta API access token (optional)
    """
    if not pixel_id:
        return error("No pixel ID provided")
    data = await make_api_request(pixel_id, access_token, {"fields": _PIXEL_FIELDS})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_pixel_stats(
    pixel_id: str,
    access_token: Optional[str] = None,
    aggregation: str = "event",
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> str:
    """Get pixel / dataset event stats.

    Args:
        pixel_id: Pixel (dataset) ID
        access_token: Meta API access token (optional)
        aggregation: event | device | host | url | event_total | event_source
        start_time: Optional Unix timestamp
        end_time: Optional Unix timestamp
    """
    if not pixel_id:
        return error("No pixel ID provided")
    params: Dict[str, Any] = {"aggregation": aggregation}
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    data = await make_api_request(f"{pixel_id}/stats", access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def list_custom_conversions(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List custom conversions for an ad account.

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        limit: Page size
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    data = await make_api_request(
        f"{account_id}/customconversions",
        access_token,
        {"fields": _CONVERSION_FIELDS, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_custom_conversion(
    conversion_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a custom conversion by ID.

    Args:
        conversion_id: Custom conversion ID
        access_token: Meta API access token (optional)
    """
    if not conversion_id:
        return error("No conversion ID provided")
    data = await make_api_request(
        conversion_id, access_token, {"fields": _CONVERSION_FIELDS}
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_custom_conversion(
    account_id: str,
    name: str,
    pixel_id: str,
    event_source_id: Optional[str] = None,
    access_token: Optional[str] = None,
    custom_event_type: str = "OTHER",
    rule: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
    default_conversion_value: Optional[float] = None,
) -> str:
    """Create a custom conversion on a pixel / dataset.

    Args:
        account_id: Ad account ID
        name: Conversion name
        pixel_id: Pixel / dataset ID (event source)
        event_source_id: Optional override for event_source_id (defaults to pixel_id)
        access_token: Meta API access token (optional)
        custom_event_type: PURCHASE, ADD_TO_CART, LEAD, COMPLETE_REGISTRATION, OTHER, ...
        rule: Optional URL / event rule JSON
            e.g. {"and": [{"or": [{"event": {"eq": "Purchase"}}]}]}
            or {"url": {"i_contains": "/thank-you"}}
        description: Optional description
        default_conversion_value: Fallback conversion value
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No conversion name provided")
    if not pixel_id:
        return error("No pixel_id provided")
    account_id = ensure_act_prefix(account_id)

    params: Dict[str, Any] = {
        "name": name,
        "pixel": str(pixel_id),
        "event_source_id": str(event_source_id or pixel_id),
        "custom_event_type": custom_event_type,
    }
    rule_obj = parse_jsonish(rule) if rule else None
    if rule_obj:
        params["rule"] = rule_obj
    if description:
        params["description"] = description
    if default_conversion_value is not None:
        params["default_conversion_value"] = default_conversion_value

    data = await make_api_request(
        f"{account_id}/customconversions", access_token, params, method="POST"
    )
    return dump(data)
