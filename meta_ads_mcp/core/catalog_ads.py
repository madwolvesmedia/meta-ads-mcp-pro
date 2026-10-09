"""Catalog / DPA / Advantage+ catalog ad creatives and ad sets."""

from typing import Any, Dict, List, Optional, Union

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import as_id_list, dump, error, parse_jsonish
from .server import mcp_server

_TEMPLATE_FIELDS = (
    "{{product.name}}",
    "{{product.price}}",
    "{{product.description}}",
    "{{product.brand}}",
    "{{product.current_price}}",
    "{{product.url}}",
    "{{product.retailer_id}}",
    "{{campaign.name}}",
    "{{campaign.id}}",
    "{{adset.name}}",
    "{{ad.name}}",
)

# Ads Manager catalog ads auto-switch carousel <-> collection.
_FORMAT_AUTOMATION = frozenset({"auto", "automatic", "carousel_collection", "format_automation"})
_CATALOG_ENHANCEMENTS = {
    "standard_enhancements": {"enroll_status": "OPT_IN"},
    "image_enhancement": {"enroll_status": "OPT_IN"},
    "image_uncrop": {"enroll_status": "OPT_IN"},
    "text_optimizations": {"enroll_status": "OPT_IN"},
    "enhance_cta": {"enroll_status": "OPT_IN"},
    "video_auto_crop": {"enroll_status": "OPT_IN"},
}


@mcp_server.tool()
@meta_api_tool
async def create_catalog_ad_creative(
    account_id: str,
    product_set_id: str,
    page_id: str,
    link: str,
    access_token: Optional[str] = None,
    name: Optional[str] = None,
    message: str = "Shop {{product.name}}",
    headline: str = "{{product.name}}",
    description: str = "{{product.price}}",
    call_to_action_type: str = "SHOP_NOW",
    format: str = "carousel",
    url_tags: Optional[str] = None,
    instagram_user_id: Optional[str] = None,
    instagram_actor_id: Optional[str] = None,
    template_data: Optional[Dict[str, Any]] = None,
    object_story_spec: Optional[Dict[str, Any]] = None,
    multi_share_end_card: bool = False,
    applink_treatment: Optional[str] = None,
    creative_features_spec: Optional[Dict[str, Any]] = None,
    enable_dynamic_media: Optional[bool] = None,
    image_layer_specs: Optional[List[Dict[str, Any]]] = None,
    collection_hero_image_hash: Optional[str] = None,
    asset_feed_spec: Optional[Dict[str, Any]] = None,
    degrees_of_freedom_spec: Optional[Dict[str, Any]] = None,
    enable_enhancements: bool = False,
) -> str:
    """Create a Advantage+ catalog / DPA ad creative (carousel, single, collection, or auto).

    Uses product_set_id plus object_story_spec.template_data so Meta fills cards from
    the catalog. Template placeholders include {{product.name}}, {{product.price}},
    {{product.description}}, {{product.brand}}, {{product.url}}.

    Formats:
      - carousel (default): dynamic product carousel from the product set
      - single: one catalog product at a time
      - collection: collection ad (optional hero image via collection_hero_image_hash)
      - auto: Ads Manager-style format automation (carousel + collection switching
        via asset_feed_spec.ad_formats CAROUSEL, COLLECTION)

    Args:
        account_id: Ad account ID (act_XXXXXXXXX)
        product_set_id: Product set to advertise
        page_id: Facebook Page ID that will publish the ad
        link: Destination URL (store URL; product URL tags still apply)
        access_token: Meta API access token (optional)
        name: Creative name
        message: Primary text (may include {{product.*}} templates)
        headline: Card title / name template
        description: Card description template
        call_to_action_type: CTA enum (SHOP_NOW, LEARN_MORE, BUY_NOW, ...)
        format: carousel | single | collection | auto (carousel+collection switching)
        url_tags: Tracking query string appended to product links
                  (e.g. utm_source=facebook&utm_medium=cpc)
        instagram_user_id: Instagram account ID for IG placements
        instagram_actor_id: Deprecated alias for instagram_user_id
        template_data: Full template_data override (merged on top of built defaults)
        object_story_spec: Full object_story_spec override
        multi_share_end_card: Show carousel end card
        applink_treatment: web_only | deeplink_with_web_fallback | deeplink_with_appstore_fallback
        creative_features_spec: Catalog enhancements
            (e.g. {"image_enhancement": {"enroll_status": "OPT_IN"}})
        enable_dynamic_media: Opt in to catalog dynamic media if True
        image_layer_specs: Optional overlay layers for catalog images
        collection_hero_image_hash: Hero image hash for collection format
        asset_feed_spec: Full asset_feed_spec override / merge (ad_formats, images, ...)
        degrees_of_freedom_spec: Full degrees_of_freedom_spec override
        enable_enhancements: Opt in to standard catalog creative enhancements
            (standard_enhancements, image_enhancement, image_uncrop, text_optimizations,
            enhance_cta, video_auto_crop) via degrees_of_freedom_spec
    """
    if not account_id:
        return error("No account ID provided")
    if not product_set_id:
        return error("No product_set_id provided")
    if not page_id:
        return error("No page_id provided")
    if not link:
        return error("No destination link provided")

    account_id = ensure_act_prefix(account_id)
    ig_id = instagram_user_id or instagram_actor_id
    fmt = (format or "carousel").strip().lower()

    built_template: Dict[str, Any] = {
        "link": link,
        "message": message,
        "name": headline,
        "description": description,
        "call_to_action": {"type": call_to_action_type, "value": {"link": link}},
        "multi_share_end_card": bool(multi_share_end_card),
    }
    if fmt == "single":
        built_template["force_single_link"] = True
    extra_template = parse_jsonish(template_data) if template_data else None
    if isinstance(extra_template, dict):
        built_template.update(extra_template)

    story = parse_jsonish(object_story_spec) if object_story_spec else None
    if not isinstance(story, dict):
        story = {"page_id": str(page_id), "template_data": built_template}
        if ig_id:
            story["instagram_user_id"] = str(ig_id)
    else:
        story.setdefault("page_id", str(page_id))
        if ig_id and "instagram_user_id" not in story:
            story["instagram_user_id"] = str(ig_id)
        if "template_data" not in story:
            story["template_data"] = built_template

    params: Dict[str, Any] = {
        "name": name or f"Catalog creative {product_set_id}",
        "product_set_id": str(product_set_id),
        "object_story_spec": story,
    }
    if url_tags:
        params["url_tags"] = url_tags
    if applink_treatment:
        params["applink_treatment"] = applink_treatment

    features = parse_jsonish(creative_features_spec) if creative_features_spec else {}
    if not isinstance(features, dict):
        features = {}
    if enable_dynamic_media or enable_enhancements:
        defaults = _CATALOG_ENHANCEMENTS if enable_enhancements else {
            "video_auto_crop": {"enroll_status": "OPT_IN"},
            "image_enhancement": {"enroll_status": "OPT_IN"},
            "enhance_cta": {"enroll_status": "OPT_IN"},
        }
        for key, value in defaults.items():
            features.setdefault(key, value)

    dof = parse_jsonish(degrees_of_freedom_spec) if degrees_of_freedom_spec else None
    if isinstance(dof, dict):
        dof = dict(dof)
        nested = dof.get("creative_features_spec")
        if isinstance(nested, dict):
            merged = dict(features)
            merged.update(nested)
            dof["creative_features_spec"] = merged
        elif features:
            dof["creative_features_spec"] = features
        params["degrees_of_freedom_spec"] = dof
    elif features:
        params["degrees_of_freedom_spec"] = {"creative_features_spec": features}

    layers = parse_jsonish(image_layer_specs) if image_layer_specs else None
    if layers:
        params["image_layer_spec"] = layers

    feed: Optional[Dict[str, Any]] = None
    if fmt in _FORMAT_AUTOMATION:
        feed = {
            "ad_formats": ["CAROUSEL", "COLLECTION"],
            "optimization_type": "REGULAR",
        }
    elif fmt == "collection":
        feed = {"ad_formats": ["COLLECTION"]}
        if collection_hero_image_hash:
            feed["images"] = [{"hash": collection_hero_image_hash}]

    extra_feed = parse_jsonish(asset_feed_spec) if asset_feed_spec else None
    if isinstance(extra_feed, dict):
        feed = {**(feed or {}), **extra_feed}
        if collection_hero_image_hash and "images" not in feed:
            feed["images"] = [{"hash": collection_hero_image_hash}]
    if feed:
        params["asset_feed_spec"] = feed

    data = await make_api_request(
        f"{account_id}/adcreatives", access_token, params, method="POST"
    )
    if isinstance(data, dict) and "id" in data:
        data["product_set_id"] = str(product_set_id)
        data["format"] = fmt
        data["template_fields"] = list(_TEMPLATE_FIELDS)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_catalog_adset(
    account_id: str,
    campaign_id: str,
    name: str,
    product_set_id: str,
    access_token: Optional[str] = None,
    product_catalog_id: Optional[str] = None,
    pixel_id: Optional[str] = None,
    custom_event_type: str = "PURCHASE",
    optimization_goal: str = "VALUE",
    billing_event: str = "IMPRESSIONS",
    destination_type: str = "WEBSITE",
    status: str = "PAUSED",
    daily_budget: Optional[int] = None,
    lifetime_budget: Optional[int] = None,
    targeting: Optional[Dict[str, Any]] = None,
    advantage_audience: bool = True,
    excluded_custom_audience_ids: Optional[Union[List[str], str]] = None,
    attribution_spec: Optional[List[Dict[str, Any]]] = None,
    bid_strategy: Optional[str] = None,
    bid_amount: Optional[int] = None,
    bid_constraints: Optional[Dict[str, Any]] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    dsa_beneficiary: Optional[str] = None,
    dsa_payor: Optional[str] = None,
) -> str:
    """Create an ad set that promotes a catalog product set (Advantage+ catalog / DPA).

    Intended for OUTCOME_SALES CBO campaigns: omit daily_budget/lifetime_budget when
    the parent campaign already has a budget. Newly created ad sets default to PAUSED.

    promoted_object is built as:
      {product_set_id, custom_event_type, optional product_catalog_id, optional pixel_id}

    Args:
        account_id: Ad account ID
        campaign_id: Parent campaign (typically OUTCOME_SALES + CBO)
        name: Ad set name
        product_set_id: Product set to promote
        access_token: Meta API access token (optional)
        product_catalog_id: Optional catalog ID (often inferred from the product set)
        pixel_id: Pixel / dataset used for conversion tracking
        custom_event_type: PURCHASE, ADD_TO_CART, VIEW_CONTENT, INITIATED_CHECKOUT, ...
        optimization_goal: VALUE (purchase value) or OFFSITE_CONVERSIONS
        billing_event: Usually IMPRESSIONS
        destination_type: WEBSITE or SHOP_AUTOMATIC
        status: Default PAUSED
        daily_budget: Ad-set budget in cents. Omit for CBO campaigns.
        lifetime_budget: Lifetime budget in cents. Omit for CBO.
        targeting: Targeting spec. Advantage+ audience is enabled by default.
        advantage_audience: Set targeting_automation.advantage_audience (default True)
        excluded_custom_audience_ids: Custom audiences to exclude (buyers, etc.)
        attribution_spec: Default 7-day click if omitted
        bid_strategy: Optional bid strategy
        bid_amount: Required for bid-cap strategies
        bid_constraints: Required for LOWEST_COST_WITH_MIN_ROAS
        start_time: ISO 8601
        end_time: ISO 8601
        dsa_beneficiary: EU DSA beneficiary
        dsa_payor: EU DSA payor
    """
    if not account_id:
        return error("No account ID provided")
    if not campaign_id:
        return error("No campaign ID provided")
    if not name:
        return error("No ad set name provided")
    if not product_set_id:
        return error("No product_set_id provided")

    account_id = ensure_act_prefix(account_id)

    promoted_object: Dict[str, Any] = {
        "product_set_id": str(product_set_id),
        "custom_event_type": custom_event_type,
    }
    if product_catalog_id:
        promoted_object["product_catalog_id"] = str(product_catalog_id)
    if pixel_id:
        promoted_object["pixel_id"] = str(pixel_id)

    targeting_spec = parse_jsonish(targeting) if targeting else None
    if not isinstance(targeting_spec, dict):
        targeting_spec = {
            "age_min": 18,
            "age_max": 65,
            "geo_locations": {"countries": ["US"]},
        }
    targeting_spec.setdefault("targeting_automation", {})
    if isinstance(targeting_spec["targeting_automation"], dict):
        targeting_spec["targeting_automation"]["advantage_audience"] = 1 if advantage_audience else 0

    excluded = as_id_list(excluded_custom_audience_ids)
    if excluded:
        targeting_spec["excluded_custom_audiences"] = [{"id": i} for i in excluded]

    attr = parse_jsonish(attribution_spec) if attribution_spec else None
    if not attr:
        attr = [{"event_type": "CLICK_THROUGH", "window_days": 7}]

    params: Dict[str, Any] = {
        "name": name,
        "campaign_id": campaign_id,
        "status": status or "PAUSED",
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "destination_type": destination_type,
        "targeting": targeting_spec,
        "promoted_object": promoted_object,
        "attribution_spec": attr,
    }
    if daily_budget is not None:
        params["daily_budget"] = str(daily_budget)
    if lifetime_budget is not None:
        params["lifetime_budget"] = str(lifetime_budget)
    if bid_strategy:
        params["bid_strategy"] = bid_strategy
    if bid_amount is not None:
        params["bid_amount"] = str(bid_amount)
    if bid_constraints:
        params["bid_constraints"] = parse_jsonish(bid_constraints)
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    if dsa_beneficiary:
        params["dsa_beneficiary"] = dsa_beneficiary
    if dsa_payor:
        params["dsa_payor"] = dsa_payor

    data = await make_api_request(
        f"{account_id}/adsets", access_token, params, method="POST"
    )
    return dump(data)
