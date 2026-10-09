"""Custom audiences, lookalikes, saved audiences, and hashed customer-list upload."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Union

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import dump, error, parse_jsonish
from .safety import require_confirm
from .server import mcp_server

_AUDIENCE_FIELDS = (
    "id,name,subtype,description,approximate_count_lower_bound,"
    "approximate_count_upper_bound,time_created,time_updated,retention_days,"
    "rule,lookalike_spec,pixel_id,operation_status,delivery_status,"
    "customer_file_source,data_source,lookalike_audience_ids"
)

_SAVED_FIELDS = (
    "id,name,description,targeting,time_created,time_updated,"
    "approximate_count_lower_bound,approximate_count_upper_bound"
)

# Fields Meta expects SHA-256 hashed for customer-list audiences.
_HASH_FIELDS = frozenset({
    "EMAIL", "PHONE", "GEN", "DOB", "LN", "FN", "FI",
    "CT", "ST", "ZIP", "COUNTRY", "MADID", "ZIP",
    "EMAIL_SHA256", "PHONE_SHA256", "MOBILE_ADVERTISER_ID",
})
_SKIP_HASH_FIELDS = frozenset({"LOOKALIKE_VALUE"})

# Current Graph API rejects `subtype` on pixel / website audiences.
# Only send it when Meta still requires the field (customer-list CUSTOM,
# LOOKALIKE, CLAIM, ENGAGEMENT, VIDEO, ...).
_WEBSITE_SUBTYPES = frozenset({"", "WEBSITE", "SITE"})


def _subtype_for_create(
    subtype: Optional[str],
    *,
    pixel_id: Optional[str] = None,
    rule: Any = None,
    lookalike_spec: Any = None,
    origin_audience_id: Optional[str] = None,
    customer_file_source: Optional[str] = None,
    claim_objective: Optional[str] = None,
) -> Optional[str]:
    """Return the subtype to send, or None to omit the field."""
    explicit = (subtype or "").strip().upper() or None

    if lookalike_spec or origin_audience_id or explicit == "LOOKALIKE":
        return "LOOKALIKE"

    websiteish = bool(pixel_id or rule) and not claim_objective
    if websiteish and (explicit is None or explicit in _WEBSITE_SUBTYPES or explicit == "CUSTOM"):
        return None
    if explicit in _WEBSITE_SUBTYPES and explicit is not None:
        return None

    if claim_objective or explicit == "CLAIM":
        return explicit or "CLAIM"
    if customer_file_source or explicit == "CUSTOM":
        return "CUSTOM"
    if explicit:
        return explicit
    return "CUSTOM"


def _product_audience_clauses(
    items: Any,
    default_days: int = 14,
) -> List[Dict[str, Any]]:
    """Normalize inclusions/exclusions to Meta product_audiences clauses.

    Accepts either Graph's `{retention_seconds, rule}` or the convenience
    shape `{event: "ViewContent"|"AddToCart"|"Purchase", retention_days: N}`.
    """
    parsed = parse_jsonish(items) if items else None
    if not parsed:
        return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    clauses: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if "rule" in item:
            entry = dict(item)
            if "retention_seconds" not in entry:
                days = entry.pop("retention_days", default_days)
                entry["retention_seconds"] = int(days) * 86400
            clauses.append(entry)
            continue
        event = item.get("event") or item.get("retention")
        if not event:
            continue
        seconds = item.get("retention_seconds")
        if seconds is None:
            days = item.get("retention_days", default_days)
            seconds = int(days) * 86400
        clauses.append({
            "retention_seconds": int(seconds),
            "rule": {"event": {"eq": str(event)}},
        })
    return clauses


def _is_sha256_hex(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", value or ""))


def normalize_and_hash(field: str, value: Any) -> str:
    """Normalize a customer-list value and SHA-256 hash it if needed.

    Already-hashed 64-char hex strings are returned as lowercase hex.
    LOOKALIKE_VALUE is never hashed. EXTERN_ID is hashed per Meta's docs
    unless it is already a hex digest.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    key = (field or "").upper()
    if _is_sha256_hex(text):
        return text.lower()
    if key in _SKIP_HASH_FIELDS:
        return text

    if key in ("EMAIL", "EMAIL_SHA256"):
        text = text.lower()
    elif key in ("PHONE", "PHONE_SHA256"):
        text = re.sub(r"\D", "", text)
    elif key in ("FN", "LN", "FI", "CT", "ST", "COUNTRY", "GEN"):
        text = text.lower()
    elif key == "ZIP":
        text = text.lower().split("-")[0].strip()
    elif key == "DOB":
        text = re.sub(r"\D", "", text)
    else:
        text = text.lower()

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_customer_rows(
    schema: List[str],
    rows: List[Any],
) -> List[List[str]]:
    """Hash each cell in a customer-list payload according to schema."""
    hashed: List[List[str]] = []
    for row in rows:
        if isinstance(row, dict):
            hashed.append([normalize_and_hash(col, row.get(col, "")) for col in schema])
        elif isinstance(row, (list, tuple)):
            hashed.append([
                normalize_and_hash(schema[i] if i < len(schema) else "EMAIL", cell)
                for i, cell in enumerate(row)
            ])
        else:
            hashed.append([normalize_and_hash(schema[0] if schema else "EMAIL", row)])
    return hashed


@mcp_server.tool()
@meta_api_tool
async def list_custom_audiences(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
    after: str = "",
    subtype: str = "",
) -> str:
    """List custom audiences for an ad account.

    Args:
        account_id: Ad account ID (act_XXXXXXXXX)
        access_token: Meta API access token (optional)
        limit: Page size
        after: Pagination cursor
        subtype: Optional subtype filter (WEBSITE, LOOKALIKE, CUSTOM, ENGAGEMENT, CLAIM, ...)
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {"fields": _AUDIENCE_FIELDS, "limit": limit}
    if after:
        params["after"] = after
    if subtype:
        params["filtering"] = [
            {"field": "subtype", "operator": "EQUAL", "value": subtype}
        ]
    data = await make_api_request(f"{account_id}/customaudiences", access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_custom_audience(
    audience_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a custom audience by ID.

    Args:
        audience_id: Custom audience ID
        access_token: Meta API access token (optional)
    """
    if not audience_id:
        return error("No audience ID provided")
    data = await make_api_request(audience_id, access_token, {"fields": _AUDIENCE_FIELDS})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_custom_audience(
    account_id: str,
    name: str,
    access_token: Optional[str] = None,
    subtype: Optional[str] = None,
    description: Optional[str] = None,
    pixel_id: Optional[str] = None,
    retention_days: Optional[int] = None,
    rule: Optional[Dict[str, Any]] = None,
    prefill: Optional[bool] = None,
    customer_file_source: Optional[str] = None,
    claim_objective: Optional[str] = None,
    content_type: Optional[str] = None,
    product_set_id: Optional[str] = None,
    event_source_group: Optional[str] = None,
    lookalike_spec: Optional[Dict[str, Any]] = None,
    origin_audience_id: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a custom audience (website/pixel, engagement, catalog, customer list, or lookalike).

    Common patterns:

    Website / pixel (visitors in last 30 days) — do **not** send subtype;
    Graph currently rejects it for pixel audiences::

        pixel_id="<pixel>", retention_days=30,
        rule={"inclusions": {"operator": "or", "rules": [{
            "event_sources": [{"id": "<pixel>", "type": "pixel"}],
            "retention_seconds": 2592000
        }]}}

    Catalog / product engagement (or use create_product_audience)::

        subtype="CLAIM", claim_objective="PRODUCT", content_type="PRODUCT",
        product_set_id="<set>"

    Customer list (create empty, then upload_custom_audience_users)::

        subtype="CUSTOM", customer_file_source="USER_PROVIDED_ONLY"

    Lookalike (or use create_lookalike_audience)::

        subtype="LOOKALIKE", origin_audience_id="<seed>",
        lookalike_spec={"country": "US", "ratio": 0.01, "type": "custom_ratio"}

    Args:
        account_id: Ad account ID
        name: Audience name
        access_token: Meta API access token (optional)
        subtype: Only sent when required. CUSTOM (customer list), LOOKALIKE,
            CLAIM, ENGAGEMENT, VIDEO, ... Omit for pixel/website audiences
            (Graph rejects subtype=WEBSITE / default CUSTOM on those).
        description: Optional description
        pixel_id: Pixel / dataset ID for website audiences
        retention_days: Retention window in days (website audiences)
        rule: Rule JSON for pixel / engagement audiences
        prefill: Include historical matching events
        customer_file_source: USER_PROVIDED_ONLY | PARTNER_PROVIDED_ONLY | BOTH_USER_AND_PARTNER_PROVIDED
        claim_objective: For catalog audiences (PRODUCT, ...)
        content_type: PRODUCT, HOTEL, FLIGHT, ...
        product_set_id: Catalog product set for catalog-based audiences
        event_source_group: Optional event source group ID
        lookalike_spec: Lookalike definition
        origin_audience_id: Seed audience for lookalikes
        extra_params: Extra Graph fields passed through
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No audience name provided")
    account_id = ensure_act_prefix(account_id)

    params: Dict[str, Any] = {"name": name}
    resolved_subtype = _subtype_for_create(
        subtype,
        pixel_id=pixel_id,
        rule=rule,
        lookalike_spec=lookalike_spec,
        origin_audience_id=origin_audience_id,
        customer_file_source=customer_file_source,
        claim_objective=claim_objective,
    )
    if resolved_subtype:
        params["subtype"] = resolved_subtype
    if description:
        params["description"] = description
    if pixel_id:
        params["pixel_id"] = str(pixel_id)
    if retention_days is not None:
        params["retention_days"] = retention_days
    rule_obj = parse_jsonish(rule) if rule else None
    if rule_obj:
        params["rule"] = rule_obj
    if prefill is not None:
        params["prefill"] = "true" if prefill else "false"
    if customer_file_source:
        params["customer_file_source"] = customer_file_source
    if claim_objective:
        params["claim_objective"] = claim_objective
    if content_type:
        params["content_type"] = content_type
    if product_set_id:
        params["product_set_id"] = str(product_set_id)
    if event_source_group:
        params["event_source_group"] = str(event_source_group)
    lookalike = parse_jsonish(lookalike_spec) if lookalike_spec else None
    if lookalike:
        params["lookalike_spec"] = lookalike
    if origin_audience_id:
        params["origin_audience_id"] = str(origin_audience_id)
    extra = parse_jsonish(extra_params) if extra_params else None
    if isinstance(extra, dict):
        extra = dict(extra)
        extra_sub = str(extra.get("subtype", "")).strip().upper()
        if extra_sub in _WEBSITE_SUBTYPES or (
            extra_sub in {"CUSTOM", "WEBSITE"}
            and (pixel_id or rule)
            and not claim_objective
            and not lookalike
            and not origin_audience_id
        ):
            extra.pop("subtype", None)
        params.update(extra)

    data = await make_api_request(
        f"{account_id}/customaudiences", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def update_custom_audience(
    audience_id: str,
    access_token: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    rule: Optional[Dict[str, Any]] = None,
    retention_days: Optional[int] = None,
    opt_out_link: Optional[str] = None,
) -> str:
    """Update a custom audience's name, description, or rule.

    Args:
        audience_id: Custom audience ID
        access_token: Meta API access token (optional)
        name: New name
        description: New description
        rule: Replacement rule JSON
        retention_days: New retention window
        opt_out_link: Data-use opt-out URL
    """
    if not audience_id:
        return error("No audience ID provided")
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if description is not None:
        params["description"] = description
    rule_obj = parse_jsonish(rule) if rule else None
    if rule_obj:
        params["rule"] = rule_obj
    if retention_days is not None:
        params["retention_days"] = retention_days
    if opt_out_link is not None:
        params["opt_out_link"] = opt_out_link
    if not params:
        return error("No update parameters provided")
    data = await make_api_request(audience_id, access_token, params, method="POST")
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def delete_custom_audience(
    audience_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Delete a custom audience. Requires confirm=true.

    Args:
        audience_id: Custom audience ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not audience_id:
        return error("No audience ID provided")
    blocked = require_confirm(confirm, "delete_custom_audience")
    if blocked:
        return blocked
    data = await make_api_request(audience_id, access_token, {}, method="DELETE")
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def upload_custom_audience_users(
    audience_id: str,
    schema: List[str],
    data: List[Any],
    confirm: bool = False,
    access_token: Optional[str] = None,
    is_raw: bool = True,
    session: Optional[Dict[str, Any]] = None,
) -> str:
    """Upload hashed customer-list rows to a custom audience. Requires confirm=true.

    Values are SHA-256 hashed locally (email lowercased, phone digits-only, etc.)
    unless they are already 64-char hex digests. No plaintext PII is sent.

    Example::

        schema=["EMAIL", "FN", "LN"]
        data=[["ada.lovelace", "Ada", "Lovelace"], ...]  # plaintext; hashed before upload

    Args:
        audience_id: Custom audience ID (CUSTOM / customer-list subtype)
        schema: Column names (EMAIL, PHONE, FN, LN, ZIP, CT, ST, COUNTRY, DOB, GEN, MADID, ...)
        data: Rows as lists (aligned to schema) or dicts keyed by schema
        confirm: Must be true — this uploads customer PII (hashed)
        access_token: Meta API access token (optional)
        is_raw: True (default) when `data` is plaintext that should be hashed
        session: Optional batch session {"session_id": n, "batch_seq": n, "last_batch_flag": bool, "estimated_num_total": n}
    """
    if not audience_id:
        return error("No audience ID provided")
    blocked = require_confirm(confirm, "upload_custom_audience_users")
    if blocked:
        return blocked
    schema_list = parse_jsonish(schema) if schema else None
    if not isinstance(schema_list, list) or not schema_list:
        return error("schema must be a non-empty list of field names")
    schema_list = [str(col).upper() for col in schema_list]
    rows = parse_jsonish(data) if data is not None else None
    if not isinstance(rows, list) or not rows:
        return error("data must be a non-empty list of rows")

    hashed_rows = hash_customer_rows(schema_list, rows) if is_raw else [
        [str(cell) for cell in (row if isinstance(row, (list, tuple)) else [row])]
        for row in rows
    ]
    payload: Dict[str, Any] = {
        "schema": schema_list,
        "data": hashed_rows,
    }
    session_obj = parse_jsonish(session) if session else None
    if isinstance(session_obj, dict):
        payload["session"] = session_obj

    result = await make_api_request(
        f"{audience_id}/users",
        access_token,
        {"payload": payload},
        method="POST",
    )
    if isinstance(result, dict):
        result["rows_uploaded"] = len(hashed_rows)
        result["hashed"] = True
    return dump(result)


@mcp_server.tool()
@meta_api_tool
async def create_lookalike_audience(
    account_id: str,
    name: str,
    origin_audience_id: str,
    country: str,
    access_token: Optional[str] = None,
    ratio: float = 0.01,
    lookalike_type: str = "custom_ratio",
    description: Optional[str] = None,
    location_spec: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a lookalike audience from a seed custom audience.

    Args:
        account_id: Ad account ID
        name: Lookalike name
        origin_audience_id: Seed custom audience ID
        country: Two-letter country code (e.g. US) unless location_spec is provided
        access_token: Meta API access token (optional)
        ratio: 0.01 = 1% lookalike (1–20% typical)
        lookalike_type: custom_ratio | similarity | reach
        description: Optional description
        location_spec: Optional GeoLocation override instead of a single country
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No audience name provided")
    if not origin_audience_id:
        return error("No origin_audience_id provided")
    account_id = ensure_act_prefix(account_id)

    spec: Dict[str, Any] = {"type": lookalike_type, "ratio": ratio}
    if location_spec:
        spec["location_spec"] = parse_jsonish(location_spec)
    elif country:
        spec["country"] = country
    else:
        return error("Provide country or location_spec")

    params: Dict[str, Any] = {
        "name": name,
        "subtype": "LOOKALIKE",
        "origin_audience_id": str(origin_audience_id),
        "lookalike_spec": spec,
    }
    if description:
        params["description"] = description
    data = await make_api_request(
        f"{account_id}/customaudiences", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_product_audience(
    account_id: str,
    name: str,
    product_set_id: str,
    access_token: Optional[str] = None,
    inclusions: Optional[Union[List[Dict[str, Any]], Dict[str, Any], str]] = None,
    exclusions: Optional[Union[List[Dict[str, Any]], Dict[str, Any], str]] = None,
    description: Optional[str] = None,
    prefill: Optional[bool] = None,
    view_content_days: Optional[int] = None,
    add_to_cart_days: Optional[int] = None,
    purchase_days: Optional[int] = None,
    exclude_purchase_days: Optional[int] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a catalog / product audience (`POST /act_X/product_audiences`).

    Inclusions and exclusions use pixel events (ViewContent, AddToCart, Purchase,
    InitiateCheckout, Search, ...) with a retention window. Convenience days
    arguments are merged into inclusions/exclusions.

    Example (viewed or added to cart in 14 days, excluding purchasers in 7 days)::

        create_product_audience(
            account_id, name, product_set_id,
            view_content_days=14, add_to_cart_days=14, exclude_purchase_days=7,
        )

    Or explicit clauses::

        inclusions=[{"event": "ViewContent", "retention_days": 14},
                    {"event": "AddToCart", "retention_days": 14}]
        exclusions=[{"event": "Purchase", "retention_days": 7}]

    Args:
        account_id: Ad account ID
        name: Audience name
        product_set_id: Catalog product set ID
        access_token: Meta API access token (optional)
        inclusions: Event clauses to include (convenience `{event, retention_days}`
            or Graph `{retention_seconds, rule}`)
        exclusions: Event clauses to exclude
        description: Optional description
        prefill: Include historical matching events
        view_content_days: Shortcut inclusion for ViewContent
        add_to_cart_days: Shortcut inclusion for AddToCart
        purchase_days: Shortcut inclusion for Purchase
        exclude_purchase_days: Shortcut exclusion for Purchase
        extra_params: Extra Graph fields passed through
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No audience name provided")
    if not product_set_id:
        return error("No product_set_id provided")
    account_id = ensure_act_prefix(account_id)

    include_clauses = _product_audience_clauses(inclusions)
    exclude_clauses = _product_audience_clauses(exclusions)

    def _add_event(target: List[Dict[str, Any]], event: str, days: Optional[int]) -> None:
        if days is None:
            return
        target.append({
            "retention_seconds": int(days) * 86400,
            "rule": {"event": {"eq": event}},
        })

    _add_event(include_clauses, "ViewContent", view_content_days)
    _add_event(include_clauses, "AddToCart", add_to_cart_days)
    _add_event(include_clauses, "Purchase", purchase_days)
    _add_event(exclude_clauses, "Purchase", exclude_purchase_days)

    if not include_clauses:
        return error(
            "Provide inclusions or view_content_days / add_to_cart_days / purchase_days",
            details="Product audiences need at least one inclusion event "
            "(typically ViewContent and/or AddToCart) with a retention window.",
        )

    params: Dict[str, Any] = {
        "name": name,
        "product_set_id": str(product_set_id),
        "inclusions": include_clauses,
    }
    if exclude_clauses:
        params["exclusions"] = exclude_clauses
    if description:
        params["description"] = description
    if prefill is not None:
        params["prefill"] = "true" if prefill else "false"
    extra = parse_jsonish(extra_params) if extra_params else None
    if isinstance(extra, dict):
        params.update(extra)

    data = await make_api_request(
        f"{account_id}/product_audiences", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def list_saved_audiences(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
    after: str = "",
) -> str:
    """List saved audiences for an ad account.

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        limit: Page size
        after: Pagination cursor
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {"fields": _SAVED_FIELDS, "limit": limit}
    if after:
        params["after"] = after
    data = await make_api_request(f"{account_id}/saved_audiences", access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_saved_audience(
    saved_audience_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get a saved audience by ID.

    Args:
        saved_audience_id: Saved audience ID
        access_token: Meta API access token (optional)
    """
    if not saved_audience_id:
        return error("No saved audience ID provided")
    data = await make_api_request(
        saved_audience_id, access_token, {"fields": _SAVED_FIELDS}
    )
    return dump(data)
