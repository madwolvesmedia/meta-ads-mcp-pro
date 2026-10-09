"""Delete/archive, native Graph /copies duplication, and bulk status updates.

Native copies use Meta's Marketing API `/copies` edge (free; no Pipeboard
paid token). The existing `duplicate_*` tools remain behind
META_ADS_ENABLE_DUPLICATION and still call Pipeboard's hosted duplicator.
"""

from typing import Any, Dict, List, Optional, Union

from .api import make_api_request, meta_api_tool, ensure_act_prefix
from .helpers import as_id_list, dump, error, parse_jsonish
from .safety import require_confirm
from .server import mcp_server

_VALID_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED", "DELETED"}


async def _set_status(object_id: str, status: str, access_token: Optional[str]) -> Dict[str, Any]:
    return await make_api_request(
        object_id, access_token, {"status": status}, method="POST"
    )


async def _delete_object(object_id: str, access_token: Optional[str]) -> Dict[str, Any]:
    return await make_api_request(object_id, access_token, {}, method="DELETE")


@mcp_server.tool()
@meta_api_tool
async def delete_campaign(
    campaign_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Permanently delete a campaign. Requires confirm=true. Prefer archive_campaign to keep history.

    Args:
        campaign_id: Campaign ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not campaign_id:
        return error("No campaign ID provided")
    blocked = require_confirm(confirm, "delete_campaign")
    if blocked:
        return blocked
    return dump(await _delete_object(campaign_id, access_token))


@mcp_server.tool()
@meta_api_tool
async def delete_adset(
    adset_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Permanently delete an ad set. Requires confirm=true.

    Args:
        adset_id: Ad set ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not adset_id:
        return error("No ad set ID provided")
    blocked = require_confirm(confirm, "delete_adset")
    if blocked:
        return blocked
    return dump(await _delete_object(adset_id, access_token))


@mcp_server.tool()
@meta_api_tool
async def delete_ad(
    ad_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Permanently delete an ad. Requires confirm=true.

    Args:
        ad_id: Ad ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not ad_id:
        return error("No ad ID provided")
    blocked = require_confirm(confirm, "delete_ad")
    if blocked:
        return blocked
    return dump(await _delete_object(ad_id, access_token))


@mcp_server.tool()
@meta_api_tool
async def delete_ad_creative(
    creative_id: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Permanently delete an ad creative. Requires confirm=true.

    Args:
        creative_id: Creative ID
        confirm: Must be true to proceed
        access_token: Meta API access token (optional)
    """
    if not creative_id:
        return error("No creative ID provided")
    blocked = require_confirm(confirm, "delete_ad_creative")
    if blocked:
        return blocked
    return dump(await _delete_object(creative_id, access_token))


@mcp_server.tool()
@meta_api_tool
async def archive_campaign(
    campaign_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Archive a campaign (status=ARCHIVED). Reversible relative to delete.

    Args:
        campaign_id: Campaign ID
        access_token: Meta API access token (optional)
    """
    if not campaign_id:
        return error("No campaign ID provided")
    return dump(await _set_status(campaign_id, "ARCHIVED", access_token))


@mcp_server.tool()
@meta_api_tool
async def archive_adset(
    adset_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Archive an ad set (status=ARCHIVED).

    Args:
        adset_id: Ad set ID
        access_token: Meta API access token (optional)
    """
    if not adset_id:
        return error("No ad set ID provided")
    return dump(await _set_status(adset_id, "ARCHIVED", access_token))


@mcp_server.tool()
@meta_api_tool
async def archive_ad(
    ad_id: str,
    access_token: Optional[str] = None,
) -> str:
    """Archive an ad (status=ARCHIVED).

    Args:
        ad_id: Ad ID
        access_token: Meta API access token (optional)
    """
    if not ad_id:
        return error("No ad ID provided")
    return dump(await _set_status(ad_id, "ARCHIVED", access_token))


def _copy_rename(name_suffix: Optional[str]) -> Optional[Dict[str, str]]:
    if not name_suffix:
        return None
    return {"rename_strategy": "DEEP_RENAME", "rename_suffix": name_suffix}


@mcp_server.tool()
@meta_api_tool
async def copy_campaign(
    campaign_id: str,
    access_token: Optional[str] = None,
    deep_copy: bool = True,
    status_option: str = "PAUSED",
    name_suffix: str = " - Copy",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> str:
    """Duplicate a campaign via Graph /copies (native, no Pipeboard paid token).

    New copies default to PAUSED (status_option=PAUSED).

    Args:
        campaign_id: Source campaign ID
        access_token: Meta API access token (optional)
        deep_copy: Copy child ad sets and ads
        status_option: PAUSED | ACTIVE | INHERITED_FROM_SOURCE
        name_suffix: Suffix applied with DEEP_RENAME
        start_time: Optional ISO 8601 start for copied ad sets
        end_time: Optional ISO 8601 end
    """
    if not campaign_id:
        return error("No campaign ID provided")
    params: Dict[str, Any] = {
        "deep_copy": bool(deep_copy),
        "status_option": status_option or "PAUSED",
    }
    rename = _copy_rename(name_suffix)
    if rename:
        params["rename_options"] = rename
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    data = await make_api_request(
        f"{campaign_id}/copies", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def copy_adset(
    adset_id: str,
    access_token: Optional[str] = None,
    campaign_id: Optional[str] = None,
    deep_copy: bool = True,
    status_option: str = "PAUSED",
    name_suffix: str = " - Copy",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> str:
    """Duplicate an ad set via Graph /copies (native, no Pipeboard paid token).

    Args:
        adset_id: Source ad set ID
        access_token: Meta API access token (optional)
        campaign_id: Optional destination campaign (defaults to the source campaign)
        deep_copy: Copy child ads
        status_option: PAUSED | ACTIVE | INHERITED_FROM_SOURCE
        name_suffix: Suffix applied with DEEP_RENAME
        start_time: Optional ISO 8601 start
        end_time: Optional ISO 8601 end
    """
    if not adset_id:
        return error("No ad set ID provided")
    params: Dict[str, Any] = {
        "deep_copy": bool(deep_copy),
        "status_option": status_option or "PAUSED",
    }
    if campaign_id:
        params["campaign_id"] = str(campaign_id)
    rename = _copy_rename(name_suffix)
    if rename:
        params["rename_options"] = rename
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    data = await make_api_request(
        f"{adset_id}/copies", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def copy_ad(
    ad_id: str,
    access_token: Optional[str] = None,
    adset_id: Optional[str] = None,
    status_option: str = "PAUSED",
    rename_prefix: Optional[str] = None,
    rename_suffix: str = " - Copy",
) -> str:
    """Duplicate an ad via Graph /copies (native, no Pipeboard paid token).

    Args:
        ad_id: Source ad ID
        access_token: Meta API access token (optional)
        adset_id: Optional destination ad set
        status_option: PAUSED | ACTIVE | INHERITED_FROM_SOURCE
        rename_prefix: Optional rename prefix
        rename_suffix: Suffix applied with DEEP_RENAME
    """
    if not ad_id:
        return error("No ad ID provided")
    params: Dict[str, Any] = {"status_option": status_option or "PAUSED"}
    if adset_id:
        params["adset_id"] = str(adset_id)
    rename: Dict[str, str] = {"rename_strategy": "DEEP_RENAME"}
    if rename_prefix:
        rename["rename_prefix"] = rename_prefix
    if rename_suffix:
        rename["rename_suffix"] = rename_suffix
    params["rename_options"] = rename
    data = await make_api_request(
        f"{ad_id}/copies", access_token, params, method="POST"
    )
    return dump(data)


@mcp_server.tool()
@meta_api_tool
async def bulk_update_status(
    object_ids: Union[List[str], str],
    status: str,
    confirm: bool = False,
    access_token: Optional[str] = None,
) -> str:
    """Set status on many campaigns, ad sets, or ads in one Graph batch.

    Status DELETED is treated as destructive and requires confirm=true.

    Args:
        object_ids: List of Graph object IDs
        status: ACTIVE | PAUSED | ARCHIVED | DELETED
        confirm: Required when status is DELETED
        access_token: Meta API access token (optional)
    """
    ids = as_id_list(object_ids)
    if not ids:
        return error("No object IDs provided")
    status_up = (status or "").upper()
    if status_up not in _VALID_STATUSES:
        return error(
            f"Invalid status '{status}'",
            valid_statuses=sorted(_VALID_STATUSES),
        )
    if status_up == "DELETED":
        blocked = require_confirm(confirm, "bulk_update_status DELETED")
        if blocked:
            return blocked

    results: List[Dict[str, Any]] = []
    # Meta batch max is 50 operations.
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        batch = []
        for oid in chunk:
            if status_up == "DELETED":
                batch.append({"method": "DELETE", "relative_url": str(oid)})
            else:
                batch.append({
                    "method": "POST",
                    "relative_url": str(oid),
                    "body": f"status={status_up}",
                })
        data = await make_api_request("", access_token, {"batch": batch}, method="POST")
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)

    return dump({
        "status": status_up,
        "count": len(ids),
        "results": results,
    })
