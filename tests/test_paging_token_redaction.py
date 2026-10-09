"""Regression: Graph paging URLs and raw payloads must not leak credentials.

Meta embeds access_token (and often appsecret_proof) in paging.next/previous.
graph_api_get and every list/raw-response tool must go through the shared
sanitizer so those secrets never appear in MCP tool output.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from meta_ads_mcp.core.api import (
    _CATALOG_MANAGEMENT_HINT,
    _redact_url,
    annotate_unapproved_api_error,
    make_api_request,
    sanitize_graph_payload,
)
from meta_ads_mcp.core.accounts import get_ad_accounts
from meta_ads_mcp.core.ads import get_ads
from meta_ads_mcp.core.adsets import get_adsets
from meta_ads_mcp.core.audiences import list_custom_audiences
from meta_ads_mcp.core.campaigns import get_campaigns
from meta_ads_mcp.core.catalogs import (
    get_ad_account_catalogs,
    get_catalog,
    list_business_catalogs,
    list_product_feeds,
    list_product_sets,
)
from meta_ads_mcp.core.catalog_ads import create_catalog_ad_creative
from meta_ads_mcp.core.extras import graph_api_get, list_ad_rules
from meta_ads_mcp.core.helpers import dump
from meta_ads_mcp.core.insights import get_insights
from meta_ads_mcp.core.pixels import list_pixels

SECRET = "EAAGtestPagingTokenSHOULDNEVERLEAK999xyz"
PROOF = "deadbeefappsecretproofvalue1234567890abcdef"
UNAPPROVED_MSG = "(#100) This application has not been approved to use this api"


def _paging_body(path="act_1/campaigns"):
    return {
        "data": [{"id": "obj_1", "name": "Sample"}],
        "paging": {
            "cursors": {"before": "BEFORE", "after": "AFTER"},
            "next": (
                f"https://graph.facebook.com/v24.0/{path}"
                f"?access_token={SECRET}&after=AFTER&limit=25"
            ),
            "previous": (
                f"https://graph.facebook.com/v24.0/{path}"
                f"?access_token={SECRET}&appsecret_proof={PROOF}&before=BEFORE"
            ),
        },
    }


def assert_no_secrets(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    assert SECRET not in text, f"access_token leaked: {text}"
    assert PROOF not in text, f"appsecret_proof leaked: {text}"
    assert "access_token=REDACTED" in text or '"access_token": "REDACTED"' in text or "paging" not in text


def _patch_graph_get(body, status=200):
    async def fake_get(self, url, params=None, headers=None, timeout=None):
        req = httpx.Request("GET", url, params=params, headers=headers)
        return httpx.Response(
            status_code=status,
            request=req,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
        )

    return patch("httpx.AsyncClient.get", new=fake_get)


def _patch_graph_post(body, status=400):
    async def fake_post(self, url, data=None, headers=None, timeout=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            status_code=status,
            request=req,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
        )

    return patch("httpx.AsyncClient.post", new=fake_post)


# ---------------------------------------------------------------------------
# Unit: sanitizer
# ---------------------------------------------------------------------------

def test_sanitize_redacts_paging_urls():
    cleaned = sanitize_graph_payload(_paging_body())
    assert_no_secrets(cleaned)
    assert "after=AFTER" in cleaned["paging"]["next"]
    assert "limit=25" in cleaned["paging"]["next"]
    assert "access_token=REDACTED" in cleaned["paging"]["next"]
    assert "appsecret_proof=REDACTED" in cleaned["paging"]["previous"]


def test_sanitize_redacts_nested_field_expansion_paging():
    payload = {
        "data": [
            {
                "id": "biz_1",
                "owned_product_catalogs": {
                    "data": [{"id": "cat_1"}],
                    "paging": {
                        "next": (
                            "https://graph.facebook.com/v24.0/biz_1/owned_product_catalogs"
                            f"?access_token={SECRET}&after=cursor"
                        )
                    },
                },
            }
        ]
    }
    cleaned = sanitize_graph_payload(payload)
    nested_next = cleaned["data"][0]["owned_product_catalogs"]["paging"]["next"]
    assert SECRET not in nested_next
    assert "access_token=REDACTED" in nested_next


def test_sanitize_redacts_sensitive_dict_keys():
    cleaned = sanitize_graph_payload(
        {"id": "1", "access_token": SECRET, "appsecret_proof": PROOF}
    )
    assert cleaned["access_token"] == "REDACTED"
    assert cleaned["appsecret_proof"] == "REDACTED"
    assert SECRET not in json.dumps(cleaned)
    assert PROOF not in json.dumps(cleaned)


def test_sanitize_redacts_known_secret_substrings():
    cleaned = sanitize_graph_payload(
        {"note": f"token was {SECRET} and proof {PROOF}"},
        secrets=[SECRET, PROOF],
    )
    assert SECRET not in cleaned["note"]
    assert PROOF not in cleaned["note"]
    assert "REDACTED" in cleaned["note"]


def test_sanitize_is_idempotent():
    once = sanitize_graph_payload(_paging_body())
    twice = sanitize_graph_payload(once)
    assert once == twice


def test_redact_url_still_replaces_with_redacted():
    url = f"https://graph.facebook.com/v24.0/me?fields=id&access_token={SECRET}"
    redacted = _redact_url(url)
    assert SECRET not in redacted
    assert "access_token=REDACTED" in redacted
    assert "fields=id" in redacted


# ---------------------------------------------------------------------------
# make_api_request success path (httpx, not patched make_api_request)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_make_api_request_success_redacts_paging():
    with _patch_graph_get(_paging_body("me/adaccounts")):
        result = await make_api_request("me/adaccounts", SECRET, {"fields": "id"})
    assert_no_secrets(result)
    assert result["data"][0]["id"] == "obj_1"


@pytest.mark.asyncio
async def test_make_api_request_text_response_redacts_token(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "super-secret-app-value")

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        req = httpx.Request("GET", url, params=params, headers=headers)
        text = (
            f"see https://graph.facebook.com/v24.0/x?access_token={SECRET}"
            f"&appsecret_proof={PROOF}"
        )
        return httpx.Response(
            status_code=200,
            request=req,
            headers={"content-type": "text/plain"},
            content=text.encode(),
        )

    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await make_api_request("x", SECRET, {})
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert PROOF not in serialized


# ---------------------------------------------------------------------------
# Last-mile: dump() + meta_api_tool even if make_api_request is mocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dump_and_decorator_redact_when_make_api_request_is_mocked():
    dirty = _paging_body("act_1/campaigns")
    with patch(
        "meta_ads_mcp.core.campaigns.make_api_request",
        new_callable=AsyncMock,
        return_value=dirty,
    ):
        result = await get_campaigns(account_id="act_1", access_token=SECRET)
    assert_no_secrets(result)
    parsed = json.loads(result)
    assert "access_token=REDACTED" in parsed["paging"]["next"]


def test_dump_helper_redacts_and_is_used_by_new_tools():
    text = dump(_paging_body("cat_1/product_sets"))
    assert_no_secrets(text)


# ---------------------------------------------------------------------------
# Tool coverage: graph_api_get + new and existing list tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller",
    [
        lambda: graph_api_get(
            object_id="act_1", edge="campaigns", access_token=SECRET, limit=25
        ),
        lambda: get_campaigns(account_id="act_1", access_token=SECRET),
        lambda: get_adsets(account_id="act_1", access_token=SECRET),
        lambda: get_ads(account_id="act_1", access_token=SECRET),
        lambda: get_ad_accounts(access_token=SECRET, limit=10),
        lambda: get_insights(object_id="act_1", access_token=SECRET, limit=10),
        lambda: list_custom_audiences(account_id="act_1", access_token=SECRET),
        lambda: list_pixels(account_id="act_1", access_token=SECRET),
        lambda: list_ad_rules(account_id="act_1", access_token=SECRET),
        lambda: list_product_sets(catalog_id="cat_1", access_token=SECRET),
        lambda: list_product_feeds(catalog_id="cat_1", access_token=SECRET),
        lambda: list_business_catalogs(business_id="biz_1", access_token=SECRET),
        lambda: get_catalog(catalog_id="cat_1", access_token=SECRET),
    ],
    ids=[
        "graph_api_get",
        "get_campaigns",
        "get_adsets",
        "get_ads",
        "get_ad_accounts",
        "get_insights",
        "list_custom_audiences",
        "list_pixels",
        "list_ad_rules",
        "list_product_sets",
        "list_product_feeds",
        "list_business_catalogs",
        "get_catalog",
    ],
)
async def test_list_tools_do_not_leak_paging_token(caller):
    with _patch_graph_get(_paging_body()):
        result = await caller()
    assert isinstance(result, str)
    assert_no_secrets(result)


# ---------------------------------------------------------------------------
# Catalog (#100) unapproved API hint
# ---------------------------------------------------------------------------

def test_annotate_unapproved_api_error_adds_catalog_management_hint():
    payload = {
        "error": {
            "message": UNAPPROVED_MSG,
            "type": "OAuthException",
            "code": 100,
        }
    }
    annotated = annotate_unapproved_api_error(payload)
    assert annotated["hint"] == _CATALOG_MANAGEMENT_HINT
    assert annotated["error"]["hint"] == _CATALOG_MANAGEMENT_HINT
    assert "catalog_management" in annotated["hint"]
    # Idempotent
    assert annotate_unapproved_api_error(annotated)["hint"] == _CATALOG_MANAGEMENT_HINT


def test_annotate_skips_unrelated_errors():
    payload = {"error": {"message": "Invalid parameter", "code": 100}}
    assert "hint" not in annotate_unapproved_api_error(payload)


@pytest.mark.asyncio
async def test_list_product_sets_hints_on_unapproved_api():
    graph_error = {
        "error": {
            "message": UNAPPROVED_MSG,
            "type": "OAuthException",
            "code": 100,
        }
    }
    with patch(
        "meta_ads_mcp.core.catalogs.make_api_request",
        new_callable=AsyncMock,
        return_value=graph_error,
    ):
        result = await list_product_sets(catalog_id="cat_1", access_token=SECRET)
    parsed = json.loads(result)
    assert parsed["hint"]
    assert "catalog_management" in parsed["hint"]
    assert UNAPPROVED_MSG in json.dumps(parsed)


@pytest.mark.asyncio
async def test_list_business_catalogs_hints_on_unapproved_api():
    graph_error = {
        "error": {
            "message": UNAPPROVED_MSG,
            "code": 100,
        }
    }
    with patch(
        "meta_ads_mcp.core.catalogs.make_api_request",
        new_callable=AsyncMock,
        return_value=graph_error,
    ):
        result = await list_business_catalogs(business_id="biz_1", access_token=SECRET)
    parsed = json.loads(result)
    assert "catalog_management" in parsed["hint"]


@pytest.mark.asyncio
async def test_get_catalog_hints_on_unapproved_api():
    graph_error = {"error": {"message": UNAPPROVED_MSG, "code": 100}}
    with patch(
        "meta_ads_mcp.core.catalogs.make_api_request",
        new_callable=AsyncMock,
        return_value=graph_error,
    ):
        result = await get_catalog(catalog_id="cat_1", access_token=SECRET)
    parsed = json.loads(result)
    assert "catalog_management" in parsed["hint"]


@pytest.mark.asyncio
async def test_get_ad_account_catalogs_hints_on_nested_unapproved_api():
    nested = {
        "error": {"message": UNAPPROVED_MSG, "code": 100},
    }
    with patch(
        "meta_ads_mcp.core.catalogs.make_api_request",
        new_callable=AsyncMock,
    ) as mock_api:
        mock_api.side_effect = [
            nested,
            {"id": "act_1", "name": "Acct"},
        ]
        result = await get_ad_account_catalogs(account_id="act_1", access_token=SECRET)
    parsed = json.loads(result)
    assert "catalog_management" in parsed.get("hint", "")
    serialized = json.dumps(parsed)
    assert UNAPPROVED_MSG in serialized


@pytest.mark.asyncio
async def test_create_catalog_ad_creative_hints_on_unapproved_api():
    graph_error = {"error": {"message": UNAPPROVED_MSG, "code": 100}}
    with patch(
        "meta_ads_mcp.core.catalog_ads.make_api_request",
        new_callable=AsyncMock,
        return_value=graph_error,
    ):
        result = await create_catalog_ad_creative(
            account_id="act_1",
            product_set_id="ps_1",
            page_id="page_1",
            link="https://example.com/shop",
            access_token=SECRET,
        )
    parsed = json.loads(result)
    assert "catalog_management" in parsed["hint"]


@pytest.mark.asyncio
async def test_http_400_unapproved_api_is_sanitized_and_hinted():
    error_body = {
        "error": {
            "message": UNAPPROVED_MSG,
            "type": "OAuthException",
            "code": 100,
        }
    }
    with _patch_graph_get(error_body, status=400):
        result = await make_api_request("cat_1/product_sets", SECRET, {"fields": "id"})
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert "catalog_management" in result.get("hint", "")
    assert "catalog_management" in result["error"].get("hint", "")
