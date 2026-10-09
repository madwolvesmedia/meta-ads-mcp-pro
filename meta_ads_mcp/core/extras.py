"""P2 Marketing API tools: rules, video upload, Instagram, leads, previews, studies, Graph GET."""

import re
from typing import Any, Dict, List, Optional, Union

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import dump, error, parse_jsonish
from .safety import require_confirm
from .server import mcp_server
from .utils import validate_public_url, BlockedURLError

_OBJECT_ID_RE = re.compile(r"^(act_)?[A-Za-z0-9_.-]+$")
_EDGE_RE = re.compile(r"^[A-Za-z0-9_]+$")

_RULE_FIELDS = (
    "id,name,status,evaluation_spec,execution_spec,created_by,created_time,"
    "updated_time,schedule_spec"
)

_LEAD_FORM_FIELDS = (
    "id,name,status,page_id,locale,created_time,questions,privacy_policy,"
    "thank_you_page,context_card,legal_content"
)


# --- Automated rules -------------------------------------------------------

@mcp_server.tool()
@meta_api_tool
async def list_ad_rules(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 50,
) -> str:
    """List automated rules for an ad account.

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        limit: Page size
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    data = await make_api_request(
        f"{account_id}/adrules_library",
        access_token,
        {"fields": _RULE_FIELDS, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_ad_rule(
    rule_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get an automated rule by ID.

    Args:
        rule_id: Ad rule ID
        access_token: Meta API access token (optional)
    """
    if not rule_id:
        return error("No rule ID provided")
    data = await make_api_request(rule_id, access_token, {"fields": _RULE_FIELDS})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_ad_rule(
    account_id: str,
    name: str,
    evaluation_spec: Dict[str, Any],
    execution_spec: Dict[str, Any],
    access_token: Optional[str] = None,
    schedule_spec: Optional[Dict[str, Any]] = None,
    status: str = "ENABLED",
) -> str:
    """Create an automated rule.

    Args:
        account_id: Ad account ID
        name: Rule name
        evaluation_spec: When the rule fires (filters + trigger)
        execution_spec: What the rule does (PAUSE, CHANGE_BUDGET, ...)
        access_token: Meta API access token (optional)
        schedule_spec: Optional schedule
        status: ENABLED | DISABLED | DELETED
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No rule name provided")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {
        "name": name,
        "evaluation_spec": parse_jsonish(evaluation_spec),
        "execution_spec": parse_jsonish(execution_spec),
        "status": status,
    }
    if schedule_spec:
        params["schedule_spec"] = parse_jsonish(schedule_spec)
    data = await make_api_request(
        f"{account_id}/adrules_library", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def update_ad_rule(
    rule_id: str,
    access_token: Optional[str] = None,
    name: Optional[str] = None,
    evaluation_spec: Optional[Dict[str, Any]] = None,
    execution_spec: Optional[Dict[str, Any]] = None,
    schedule_spec: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
) -> str:
    """Update an automated rule.

    Args:
        rule_id: Ad rule ID
        access_token: Meta API access token (optional)
        name: New name
        evaluation_spec: Replacement evaluation spec
        execution_spec: Replacement execution spec
        schedule_spec: Replacement schedule
        status: ENABLED | DISABLED | DELETED
    """
    if not rule_id:
        return error("No rule ID provided")
    params: Dict[str, Any] = {}
    if name is not None:
        params["name"] = name
    if evaluation_spec is not None:
        params["evaluation_spec"] = parse_jsonish(evaluation_spec)
    if execution_spec is not None:
        params["execution_spec"] = parse_jsonish(execution_spec)
    if schedule_spec is not None:
        params["schedule_spec"] = parse_jsonish(schedule_spec)
    if status is not None:
        params["status"] = status
    if not params:
        return error("No update parameters provided")
    data = await make_api_request(rule_id, access_token, params, method="POST")
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def delete_ad_rule(
    rule_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Delete an automated rule. Requires confirm=true.

    Args:
        rule_id: Ad rule ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not rule_id:
        return error("No rule ID provided")
    blocked = require_confirm(confirm, "delete_ad_rule")
    if blocked:
        return blocked
    data = await make_api_request(rule_id, access_token, {}, method="DELETE")
    return dump(data)


# --- Video upload ----------------------------------------------------------

@mcp_server.tool()
@meta_api_tool
async def upload_ad_video(
    account_id: str,
    access_token: Optional[str] = None,
    file_url: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    name: Optional[str] = None,
    upload_phase: Optional[str] = None,
    upload_session_id: Optional[str] = None,
    start_offset: Optional[Union[int, str]] = None,
    video_file_chunk: Optional[str] = None,
    file_size: Optional[int] = None,
) -> str:
    """Upload a video to an ad account, from a public URL or via chunked sessions.

    From URL (simplest)::

        upload_ad_video(account_id, file_url="https://cdn.example.com/clip.mp4", title="Hero")

    Chunked (start → transfer → finish)::

        start:   upload_phase="start", file_size=<bytes>
        transfer: upload_phase="transfer", upload_session_id, start_offset, video_file_chunk
        finish:  upload_phase="finish", upload_session_id, title=...

    Poll processing with get_ad_video(video_id=..., account_id=...).

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        file_url: Public http(s) URL for Meta to fetch (SSRF-guarded)
        title: Video title
        description: Video description
        name: Alternate title field
        upload_phase: start | transfer | finish for chunked upload
        upload_session_id: Session from the start phase
        start_offset: Chunk offset
        video_file_chunk: Chunk payload for transfer phase
        file_size: Total bytes for start phase
    """
    if not account_id:
        return error("No account ID provided")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {}
    if file_url:
        try:
            validate_public_url(file_url)
        except BlockedURLError as exc:
            return error(str(exc), code="blocked_url")
        params["file_url"] = file_url
    if title:
        params["title"] = title
    if name:
        params["name"] = name
    if description:
        params["description"] = description
    if upload_phase:
        params["upload_phase"] = upload_phase
    if upload_session_id:
        params["upload_session_id"] = upload_session_id
    if start_offset is not None:
        params["start_offset"] = start_offset
    if video_file_chunk is not None:
        params["video_file_chunk"] = video_file_chunk
    if file_size is not None:
        params["file_size"] = file_size
    if not params:
        return error("Provide file_url or chunked upload_phase parameters")
    data = await make_api_request(
        f"{account_id}/advideos", access_token, params, method="POST"
    )
    return dump(data)


# --- Instagram -------------------------------------------------------------

@mcp_server.tool()
@meta_api_tool
async def list_instagram_accounts(
    account_id: str = "",
    page_id: str = "",
    access_token: Optional[str] = None,
) -> str:
    """List Instagram accounts connected to an ad account and/or a Page.

    Args:
        account_id: Ad account ID (lists Instagram accounts usable for ads)
        page_id: Facebook Page ID (lists connected IG accounts / instagram_business_account)
        access_token: Meta API access token (optional)
    """
    if not account_id and not page_id:
        return error("Provide account_id and/or page_id")
    result: Dict[str, Any] = {}
    if account_id:
        act = ensure_act_prefix(account_id)
        result["ad_account_instagram_accounts"] = await make_api_request(
            f"{act}/instagram_accounts",
            access_token,
            {"fields": "id,username,profile_pic,followed_by_count"},
        )
    if page_id:
        result["page"] = await make_api_request(
            page_id,
            access_token,
            {"fields": "id,name,instagram_business_account{id,username,profile_picture_url},connected_instagram_account"},
        )
        result["page_instagram_accounts"] = await make_api_request(
            f"{page_id}/instagram_accounts",
            access_token,
            {"fields": "id,username,profile_pic"},
        )
    return dump(result)


# --- Lead forms ------------------------------------------------------------

@mcp_server.tool()
@meta_api_tool
async def list_lead_forms(
    page_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
) -> str:
    """List Instant Forms on a Facebook Page.

    Args:
        page_id: Page ID
        access_token: Meta API access token (optional)
        limit: Page size
    """
    if not page_id:
        return error("No page ID provided")
    data = await make_api_request(
        f"{page_id}/leadgen_forms",
        access_token,
        {"fields": _LEAD_FORM_FIELDS, "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_lead_form(
    form_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get an Instant Form by ID.

    Args:
        form_id: Lead form ID
        access_token: Meta API access token (optional)
    """
    if not form_id:
        return error("No form ID provided")
    data = await make_api_request(form_id, access_token, {"fields": _LEAD_FORM_FIELDS})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_lead_form(
    page_id: str,
    name: str,
    questions: List[Dict[str, Any]],
    access_token: Optional[str] = None,
    privacy_policy_url: Optional[str] = None,
    locale: str = "en_US",
    thank_you_page: Optional[Dict[str, Any]] = None,
    context_card: Optional[Dict[str, Any]] = None,
) -> str:
    """Create an Instant Form on a Page.

    Args:
        page_id: Page ID
        name: Form name
        questions: List of question objects (e.g. [{"type": "EMAIL"}, {"type": "FULL_NAME"}])
        access_token: Meta API access token (optional)
        privacy_policy_url: Required privacy policy URL
        locale: Form locale
        thank_you_page: Optional thank-you page spec
        context_card: Optional intro card
    """
    if not page_id:
        return error("No page ID provided")
    if not name:
        return error("No form name provided")
    qs = parse_jsonish(questions)
    if not qs:
        return error("questions is required")
    params: Dict[str, Any] = {"name": name, "questions": qs, "locale": locale}
    if privacy_policy_url:
        params["privacy_policy"] = {"url": privacy_policy_url}
    if thank_you_page:
        params["thank_you_page"] = parse_jsonish(thank_you_page)
    if context_card:
        params["context_card"] = parse_jsonish(context_card)
    data = await make_api_request(
        f"{page_id}/leadgen_forms", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_lead_form_leads(
    form_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
    after: str = "",
    from_date: Optional[int] = None,
) -> str:
    """Download leads submitted to an Instant Form.

    Args:
        form_id: Lead form ID
        access_token: Meta API access token (optional)
        limit: Page size
        after: Pagination cursor
        from_date: Optional Unix timestamp lower bound
    """
    if not form_id:
        return error("No form ID provided")
    params: Dict[str, Any] = {
        "fields": "id,created_time,ad_id,adset_id,campaign_id,form_id,is_organic,field_data",
        "limit": limit,
    }
    if after:
        params["after"] = after
    if from_date is not None:
        params["filtering"] = [
            {"field": "time_created", "operator": "GREATER_THAN", "value": from_date}
        ]
    data = await make_api_request(f"{form_id}/leads", access_token, params)
    return dump(data)


# --- Funding / previews / review / activity / studies ----------------------

@mcp_server.tool()
@meta_api_tool
async def get_account_funding(
    account_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Read ad account spend cap, balance, funding sources, and related limits.

    Amounts follow Meta's smallest-currency-unit convention (cents for USD).

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    fields = (
        "id,name,account_status,currency,amount_spent,balance,spend_cap,"
        "min_daily_budget,is_prepay_account,funding_source_details,"
        "disable_reason,tax_id_status,timezone_name,business{id,name}"
    )
    data = await make_api_request(account_id, access_token, {"fields": fields})
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def generate_ad_preview(
    account_id: str = "",
    ad_id: str = "",
    creative_id: str = "",
    ad_format: str = "DESKTOP_FEED_STANDARD",
    access_token: Optional[str] = None,
    creative: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate an ad preview iframe (generatepreviews / ad previews).

    Provide ad_id, creative_id, or a creative spec plus account_id.

    Args:
        account_id: Required when previewing an unpublished creative spec
        ad_id: Existing ad to preview
        creative_id: Existing creative to preview
        ad_format: DESKTOP_FEED_STANDARD, INSTAGRAM_STANDARD, MOBILE_FEED_STANDARD,
            INSTAGRAM_STORY, FACEBOOK_STORY_MOBILE, AUDIENCE_NETWORK_INSTREAM_VIDEO, ...
        access_token: Meta API access token (optional)
        creative: Creative spec dict for unpublished previews
    """
    if ad_id:
        data = await make_api_request(
            f"{ad_id}/previews", access_token, {"ad_format": ad_format}
        )
        return dump(data)
    if not account_id:
        return error("Provide ad_id, or account_id with creative_id/creative")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {"ad_format": ad_format}
    if creative_id:
        params["creative"] = {"creative_id": str(creative_id)}
    elif creative:
        params["creative"] = parse_jsonish(creative)
    else:
        return error("Provide creative_id or creative spec when not using ad_id")
    data = await make_api_request(
        f"{account_id}/generatepreviews", access_token, params
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_ad_review_info(
    ad_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get ad review / rejection information (issues_info, ad_review_feedback).

    Args:
        ad_id: Ad ID
        access_token: Meta API access token (optional)
    """
    if not ad_id:
        return error("No ad ID provided")
    data = await make_api_request(
        ad_id,
        access_token,
        {
            "fields": (
                "id,name,status,effective_status,configured_status,"
                "issues_info,ad_review_feedback,recommendations,preview_shareable_link"
            )
        },
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_account_activities(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
    after: str = "",
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> str:
    """Get ad account activity history.

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        limit: Page size
        after: Pagination cursor
        since: Optional Unix timestamp
        until: Optional Unix timestamp
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {
        "fields": "event_time,event_type,actor_id,actor_name,extra_data,object_id,object_name,translated_event_type",
        "limit": limit,
    }
    if after:
        params["after"] = after
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until
    data = await make_api_request(f"{account_id}/activities", access_token, params)
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def list_ad_studies(
    account_id: str,
    access_token: Optional[str] = None,
    limit: int = 25,
) -> str:
    """List A/B tests (ad studies) for an ad account.

    Args:
        account_id: Ad account ID
        access_token: Meta API access token (optional)
        limit: Page size
    """
    if not account_id:
        return error("No account ID specified")
    account_id = ensure_act_prefix(account_id)
    data = await make_api_request(
        f"{account_id}/ad_studies",
        access_token,
        {"fields": "id,name,type,start_time,end_time,description,cells,status", "limit": limit},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def get_ad_study(
    study_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Get an A/B test (ad study) by ID.

    Args:
        study_id: Ad study ID
        access_token: Meta API access token (optional)
    """
    if not study_id:
        return error("No study ID provided")
    data = await make_api_request(
        study_id,
        access_token,
        {"fields": "id,name,type,start_time,end_time,description,cells,status,results,confidence"},
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def create_ad_study(
    account_id: str,
    name: str,
    type: str,
    cells: List[Dict[str, Any]],
    access_token: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    description: Optional[str] = None,
) -> str:
    """Create an A/B test (ad study). New studies should be reviewed before going live.

    Args:
        account_id: Ad account ID
        name: Study name
        type: SPLIT_TEST | LIFT | ...
        cells: Study cells (treatment groups)
        access_token: Meta API access token (optional)
        start_time: Unix timestamp
        end_time: Unix timestamp
        description: Optional description
    """
    if not account_id:
        return error("No account ID provided")
    if not name:
        return error("No study name provided")
    account_id = ensure_act_prefix(account_id)
    params: Dict[str, Any] = {
        "name": name,
        "type": type,
        "cells": parse_jsonish(cells),
    }
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if description:
        params["description"] = description
    data = await make_api_request(
        f"{account_id}/ad_studies", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def graph_api_get(
    object_id: str,
    access_token: Optional[str] = None,
    fields: str = "",
    edge: str = "",
    extra_params: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> str:
    """Read-only generic Graph API GET. Does not allow writes or arbitrary URLs.

    Use this for fields/edges that do not yet have a dedicated tool. object_id
    must be an act_ / numeric / alphanumeric Graph ID. edge, if set, must be a
    single edge name (no slashes).

    Args:
        object_id: Graph object ID (e.g. act_123, campaign id, catalog id)
        access_token: Meta API access token (optional)
        fields: Comma-separated fields
        edge: Optional connection name (e.g. "ads", "product_sets")
        extra_params: Additional query params (filtering, date_preset, ...)
        limit: Optional page size
    """
    if not object_id or not _OBJECT_ID_RE.match(object_id):
        return error(
            "Invalid object_id",
            details="object_id must be a Graph ID (letters, digits, underscore, dot, hyphen; optional act_ prefix). URLs and nested paths are rejected.",
        )
    if edge:
        if not _EDGE_RE.match(edge):
            return error(
                "Invalid edge",
                details="edge must be a single Graph connection name with no slashes.",
            )
        endpoint = f"{object_id}/{edge}"
    else:
        endpoint = object_id

    params: Dict[str, Any] = {}
    if fields:
        params["fields"] = fields
    if limit is not None:
        params["limit"] = limit
    extra = parse_jsonish(extra_params) if extra_params else None
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key in {"access_token", "appsecret_proof"}:
                continue
            params[key] = value

    data = await make_api_request(endpoint, access_token, params, method="GET")
    return dump(data)
