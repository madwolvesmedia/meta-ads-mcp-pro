"""Product catalog, product set, feed, and diagnostics tools."""

from typing import Any, Dict, List, Optional, Union

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import dump, error, parse_jsonish
from .safety import require_confirm
from .server import mcp_server

_CATALOG_FIELDS = (
    "id,name,vertical,product_count,feed_count,business{id,name},"
    "default_image_url,fallback_image_url,is_catalog_segment,"
    "catalog_store,external_event_sources"
)

_PRODUCT_SET_FIELDS = (
    "id,name,filter,product_count,latest_metadata,live_metadata,"
    "product_catalog{id,name}"
)

_PRODUCT_FIELDS = (
    "id,retailer_id,name,description,availability,condition,brand,"
    "category,price,currency,url,image_url,visibility,custom_label_0,"
    "custom_label_1,custom_label_2,custom_label_3,custom_label_4,"
    "product_type,additional_image_urls"
)

_FEED_FIELDS = (
    "id,name,schedule,url,product_count,latest_upload,"
    "ingestion_fetch_interval,update_schedule"
)


def _filter_param(filter_rules: Any) -> Optional[str]:
    """Normalize product-set / product filter rules to a JSON string."""
    if filter_rules is None or filter_rules == "":
        return None
    parsed = parse_jsonish(filter_rules)
    if isinstance(parsed, dict):
        import json
        return json.dumps(parsed)
    if isinstance(parsed, str):
        return parsed
    import json
    return json.dumps(parsed)


@mcp_server.tool()
@meta_api_tool
async def list_businesses(
    access_token: Optional[str] = None,
    user_id: str = "me",
    limit: int = 50,
) -> str:
    """List Business Managers the current user can access.

    Needed to discover product catalogs owned by a business.

    Args:
        access_token: Meta API access token (optional)
        user_id: Meta user ID or "me" (default)
        limit: Maximum businesses to return
    """
    data = await make_api_request(
        f"{user_id}/businesses",
        access_token,
        {
            "fields": "id,name,created_time,primary_page,verification_status,profile_picture_uri",
            "limit": limit,
        },
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def list_business_catalogs(
    business_id: str = "",
    access_token: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List product catalogs owned by a Business Manager.

    If business_id is omitted, catalogs for every accessible business are returned.

    Args:
        business_id: Business Manager ID. Empty = all businesses the token can see.
        access_token: Meta API access token (optional)
        limit: Maximum catalogs per business
    """
    fields = f"id,name,owned_product_catalogs.limit({limit}){{{_CATALOG_FIELDS}}}"
    if business_id:
        data = await make_api_request(
            f"{business_id}/owned_product_catalogs",
            access_token,
            {"fields": _CATALOG_FIELDS, "limit": limit},
        )
        return dump(data)

    businesses = await make_api_request(
        "me/businesses",
        access_token,
        {"fields": fields, "limit": 50},
    )
    return dump(businesses)


@mcp_server.tool()
@meta_api_tool
async def get_catalog(
    catalog_id: str,
    access_token: Optional[str] = None,
    fields: str = "",
) -> str:
    """Get a product catalog by ID.

    Args:
        catalog_id: Product catalog ID
        access_token: Meta API access token (optional)
        fields: Optional comma-separated Graph fields (replaces the default set)
    """
    if not catalog_id:
        return error("No catalog ID provided")
    data = await make_api_request(
        catalog_id,
        access_token,
        {"fields": fields.strip() if fields and fields.strip() else _CATALOG_FIELDS},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_ad_account_catalogs(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
) -> str:
    """Find product catalogs assigned to or reachable from an ad account.

    Tries the account's assigned_product_catalogs edge, then catalogs owned by
    the account's Business Manager. Use this to pick a catalog for DPA / Advantage+
    catalog ads.

    Args:
        account_id: Meta Ads account ID (act_XXXXXXXXX)
        access_token: Meta API access token (optional)
        limit: Maximum catalogs to return per source
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)

    assigned = await make_api_request(
        f"{account_id}/assigned_product_catalogs",
        access_token,
        {"fields": _CATALOG_FIELDS, "limit": limit},
    )

    account = await make_api_request(
        account_id,
        access_token,
        {"fields": f"id,name,business{{id,name,owned_product_catalogs.limit({limit}){{{_CATALOG_FIELDS}}}}}"},
    )

    result: Dict[str, Any] = {
        "account_id": account_id,
        "assigned_product_catalogs": assigned,
        "business": account.get("business") if isinstance(account, dict) else None,
    }
    if isinstance(account, dict) and "error" in account and "business" not in account:
        result["account_lookup"] = account
    return dump(result)


@mcp_server.tool()
@meta_api_tool
async def get_pixel_catalogs(
    pixel_id: str,
    access_token: Optional[str] = None,
    business_id: str = "",
    limit: int = 50,
) -> str:
    """Find product catalogs that use a given pixel / dataset as an event source.

    Args:
        pixel_id: Pixel (dataset) ID
        access_token: Meta API access token (optional)
        business_id: Optional Business Manager to search. If empty, searches
            businesses accessible to the current user.
        limit: Maximum catalogs to inspect per business
    """
    if not pixel_id:
        return error("No pixel ID provided")

    pixel = await make_api_request(
        pixel_id,
        access_token,
        {"fields": "id,name,owner_ad_account,owner_business,is_unavailable,last_fired_time"},
    )

    businesses: List[str] = []
    if business_id:
        businesses = [business_id]
    else:
        owner = pixel.get("owner_business", {}) if isinstance(pixel, dict) else {}
        if isinstance(owner, dict) and owner.get("id"):
            businesses = [owner["id"]]
        else:
            biz_data = await make_api_request(
                "me/businesses", access_token, {"fields": "id", "limit": 50}
            )
            businesses = [b.get("id") for b in biz_data.get("data", []) if b.get("id")]

    matches: List[Dict[str, Any]] = []
    inspected = 0
    for biz in businesses:
        catalogs = await make_api_request(
            f"{biz}/owned_product_catalogs",
            access_token,
            {"fields": "id,name,vertical,product_count,external_event_sources", "limit": limit},
        )
        for catalog in catalogs.get("data", []) if isinstance(catalogs, dict) else []:
            inspected += 1
            sources = catalog.get("external_event_sources", {})
            source_rows = sources.get("data", sources) if isinstance(sources, dict) else sources
            if not isinstance(source_rows, list):
                source_rows = []
            source_ids = {str(s.get("id")) for s in source_rows if isinstance(s, dict)}
            if pixel_id in source_ids or str(pixel_id) in source_ids:
                matches.append(catalog)

    return dump({
        "pixel": pixel,
        "matching_catalogs": matches,
        "businesses_searched": businesses,
        "catalogs_inspected": inspected,
    })


@mcp_server.tool()
@meta_api_tool
async def list_product_sets(
    catalog_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
    after: str = "",
) -> str:
    """List product sets in a catalog.

    Args:
        catalog_id: Product catalog ID
        access_token: Meta API access token (optional)
        limit: Maximum product sets to return
        after: Pagination cursor
    """
    if not catalog_id:
        return error("No catalog ID provided")
    params: Dict[str, Any] = {"fields": _PRODUCT_SET_FIELDS, "limit": limit}
    if after:
        params["after"] = after
    data = await make_api_request(f"{catalog_id}/product_sets", access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_product_set(
    product_set_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a product set, including its filter rules and product count.

    Args:
        product_set_id: Product set ID
        access_token: Meta API access token (optional)
    """
    if not product_set_id:
        return error("No product set ID provided")
    data = await make_api_request(
        product_set_id, access_token, {"fields": _PRODUCT_SET_FIELDS}
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_product_set(
    catalog_id: str,
    name: str,
    filter_rules: Optional[Union[Dict[str, Any], str]] = None,
    access_token: Optional[str] = None,
) -> str:
    """Create a product set in a catalog using filter rules.

    Filter rules use Meta's product-set filter JSON. Examples:

      {"product_type": {"eq": "Shoes"}}
      {"brand": {"eq": "Acme"}}
      {"custom_label_0": {"eq": "summer"}}
      {"availability": {"eq": "in stock"}}
      {"retailer_id": {"is_any": ["sku-1", "sku-2"]}}
      {"price_amount": {"gt": "1000"}}
      {"and": [{"custom_label_0": {"eq": "sale"}}, {"brand": {"eq": "Acme"}}]}

    Supported fields include product_type, brand, retailer_id, price / price_amount,
    availability, category, name, description, and custom_label_0 through custom_label_4.
    Operators include eq, neq, gt, gte, lt, lte, contains, not_contains, is_any,
    is_unknown, i_contains.

    An empty filter (omit filter_rules) creates a set of all catalog products.

    Args:
        catalog_id: Product catalog ID
        name: Product set name
        filter_rules: Filter object (dict or JSON string)
        access_token: Meta API access token (optional)
    """
    if not catalog_id:
        return error("No catalog ID provided")
    if not name:
        return error("No product set name provided")
    params: Dict[str, Any] = {"name": name}
    encoded = _filter_param(filter_rules)
    if encoded is not None:
        params["filter"] = encoded
    data = await make_api_request(
        f"{catalog_id}/product_sets", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def update_product_set(
    product_set_id: str,
    name: Optional[str] = None,
    filter_rules: Optional[Union[Dict[str, Any], str]] = None,
    access_token: Optional[str] = None,
) -> str:
    """Update a product set's name and/or filter rules.

    Args:
        product_set_id: Product set ID
        name: New name
        filter_rules: Replacement filter object (dict or JSON string)
        access_token: Meta API access token (optional)
    """
    if not product_set_id:
        return error("No product set ID provided")
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    encoded = _filter_param(filter_rules)
    if encoded is not None:
        params["filter"] = encoded
    if not params:
        return error("No update parameters provided")
    data = await make_api_request(product_set_id, access_token, params, method="POST")
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def delete_product_set(
    product_set_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Permanently delete a product set. Requires confirm=true.

    Args:
        product_set_id: Product set ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not product_set_id:
        return error("No product set ID provided")
    blocked = require_confirm(confirm, "delete_product_set")
    if blocked:
        return blocked
    data = await make_api_request(product_set_id, access_token, {}, method="DELETE")
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def preview_product_set(
    product_set_id: str = "",
    catalog_id: str = "",
    filter_rules: Optional[Union[Dict[str, Any], str]] = None,
    access_token: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Preview products and count matching a product set or an ad-hoc filter.

    Provide either product_set_id (uses the saved set) or catalog_id + filter_rules
    (counts/previews without creating a set).

    Args:
        product_set_id: Existing product set ID
        catalog_id: Catalog ID when previewing an ad-hoc filter
        filter_rules: Ad-hoc filter (used with catalog_id)
        access_token: Meta API access token (optional)
        limit: Number of sample products to return
    """
    if product_set_id:
        meta = await make_api_request(
            product_set_id, access_token, {"fields": _PRODUCT_SET_FIELDS}
        )
        products = await make_api_request(
            f"{product_set_id}/products",
            access_token,
            {"fields": _PRODUCT_FIELDS, "limit": limit},
        )
        return dump({
            "product_set": meta,
            "product_count": meta.get("product_count") if isinstance(meta, dict) else None,
            "sample_products": products,
        })

    if not catalog_id:
        return error("Provide product_set_id, or catalog_id with optional filter_rules")

    params: Dict[str, Any] = {"fields": _PRODUCT_FIELDS, "limit": limit}
    encoded = _filter_param(filter_rules)
    if encoded is not None:
        params["filter"] = encoded
    products = await make_api_request(f"{catalog_id}/products", access_token, params)
    sample = products.get("data", []) if isinstance(products, dict) else []
    return dump({
        "catalog_id": catalog_id,
        "filter": parse_jsonish(filter_rules) if filter_rules else None,
        "sample_count": len(sample) if isinstance(sample, list) else 0,
        "sample_products": products,
        "note": "Ad-hoc filter preview returns a page of matching products. Create a product set to get an official product_count.",
    })


@mcp_server.tool()
@meta_api_tool
async def list_catalog_products(
    catalog_id: str = "",
    product_set_id: str = "",
    access_token: Optional[str] = None,
    filter_rules: Optional[Union[Dict[str, Any], str]] = None,
    retailer_id: str = "",
    limit: int = 25,
    after: str = "",
) -> str:
    """List products in a catalog or product set, with optional filters.

    Args:
        catalog_id: Product catalog ID (required unless product_set_id is set)
        product_set_id: If set, list products inside this set instead of the whole catalog
        access_token: Meta API access token (optional)
        filter_rules: Optional product filter JSON
        retailer_id: Shortcut filter for a single retailer_id / SKU
        limit: Page size
        after: Pagination cursor
    """
    if product_set_id:
        endpoint = f"{product_set_id}/products"
    elif catalog_id:
        endpoint = f"{catalog_id}/products"
    else:
        return error("Provide catalog_id or product_set_id")

    params: Dict[str, Any] = {"fields": _PRODUCT_FIELDS, "limit": limit}
    if after:
        params["after"] = after

    rules = parse_jsonish(filter_rules) if filter_rules else None
    if retailer_id:
        sku_filter = {"retailer_id": {"eq": retailer_id}}
        if isinstance(rules, dict):
            rules = {"and": [rules, sku_filter]}
        else:
            rules = sku_filter
    encoded = _filter_param(rules)
    if encoded is not None:
        params["filter"] = encoded

    data = await make_api_request(endpoint, access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def list_product_feeds(
    catalog_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
) -> str:
    """List product feeds attached to a catalog.

    Args:
        catalog_id: Product catalog ID
        access_token: Meta API access token (optional)
        limit: Maximum feeds to return
    """
    if not catalog_id:
        return error("No catalog ID provided")
    data = await make_api_request(
        f"{catalog_id}/product_feeds",
        access_token,
        {"fields": _FEED_FIELDS, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_product_feed(
    feed_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a product feed and its latest upload summary.

    Args:
        feed_id: Product feed ID
        access_token: Meta API access token (optional)
    """
    if not feed_id:
        return error("No feed ID provided")
    data = await make_api_request(feed_id, access_token, {"fields": _FEED_FIELDS})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_feed_upload_status(
    feed_id: str = "",
    upload_id: str = "",
    access_token: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Get product-feed upload history, or a single upload by ID.

    Args:
        feed_id: Product feed ID (lists recent uploads)
        upload_id: Specific upload ID
        access_token: Meta API access token (optional)
        limit: Number of uploads to list when using feed_id
    """
    upload_fields = (
        "id,start_time,end_time,filename,url,num_detected_items,"
        "num_invalid_items,num_persisted_items,error_count,warning_count"
    )
    if upload_id:
        data = await make_api_request(upload_id, access_token, {"fields": upload_fields})
        return dump(data)
    if not feed_id:
        return error("Provide feed_id or upload_id")
    data = await make_api_request(
        f"{feed_id}/uploads",
        access_token,
        {"fields": upload_fields, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_catalog_diagnostics(
    catalog_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
) -> str:
    """Get catalog diagnostics: issues, event sources, and high-level counts.

    Args:
        catalog_id: Product catalog ID
        access_token: Meta API access token (optional)
        limit: Maximum diagnostic rows
    """
    if not catalog_id:
        return error("No catalog ID provided")

    summary = await make_api_request(
        catalog_id,
        access_token,
        {"fields": _CATALOG_FIELDS},
    )
    diagnostics = await make_api_request(
        f"{catalog_id}/diagnostics",
        access_token,
        {"limit": limit},
    )
    event_stats = await make_api_request(
        f"{catalog_id}/event_stats",
        access_token,
        {"limit": limit},
    )
    return dump({
        "catalog": summary,
        "diagnostics": diagnostics,
        "event_stats": event_stats,
    })
