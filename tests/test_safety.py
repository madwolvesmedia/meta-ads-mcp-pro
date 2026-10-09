"""Tests for read-only mode, budget caps, confirm, and audit logging."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meta_ads_mcp.core import safety
from meta_ads_mcp.core.api import make_api_request
from meta_ads_mcp.core.audiences import normalize_and_hash
from meta_ads_mcp.core.lifecycle import delete_campaign, bulk_update_status
from meta_ads_mcp.core.catalogs import delete_product_set


def _mock_response(payload=None):
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.json.return_value = payload or {"success": True}
    response.raise_for_status.return_value = None
    return response


@pytest.fixture
def mock_httpx():
    with patch("meta_ads_mcp.core.api.httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(return_value=_mock_response())
        client.post = AsyncMock(return_value=_mock_response({"id": "123"}))
        client.delete = AsyncMock(return_value=_mock_response({"success": True}))
        client.put = AsyncMock(return_value=_mock_response())
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        yield client


@pytest.mark.asyncio
async def test_read_only_blocks_post(mock_httpx, monkeypatch):
    monkeypatch.setenv("META_ADS_READ_ONLY", "true")
    result = await make_api_request("act_1/campaigns", "token", {"name": "x"}, method="POST")
    assert result["error"]["code"] == "read_only"
    mock_httpx.post.assert_not_called()


@pytest.mark.asyncio
async def test_read_only_allows_get(mock_httpx, monkeypatch):
    monkeypatch.setenv("META_ADS_READ_ONLY", "1")
    result = await make_api_request("act_1", "token", {"fields": "id"}, method="GET")
    assert result == {"success": True}
    mock_httpx.get.assert_called()


@pytest.mark.asyncio
async def test_read_only_allows_insights_async_when_mutation_false(mock_httpx, monkeypatch):
    monkeypatch.setenv("META_ADS_READ_ONLY", "yes")
    result = await make_api_request(
        "act_1/insights", "token", {"date_preset": "last_7d"}, method="POST", mutation=False
    )
    assert result.get("id") == "123" or result.get("success") is True
    mock_httpx.post.assert_called()


@pytest.mark.asyncio
async def test_budget_cap_rejects_high_daily_budget(mock_httpx, monkeypatch):
    monkeypatch.setenv("META_ADS_MAX_DAILY_BUDGET", "5000")
    result = await make_api_request(
        "act_1/campaigns", "token", {"name": "x", "daily_budget": "10000"}, method="POST"
    )
    assert result["error"]["code"] == "budget_cap"
    mock_httpx.post.assert_not_called()


@pytest.mark.asyncio
async def test_budget_cap_allows_under_limit(mock_httpx, monkeypatch):
    monkeypatch.setenv("META_ADS_MAX_DAILY_BUDGET", "50000")
    result = await make_api_request(
        "act_1/campaigns", "token", {"name": "x", "daily_budget": "10000"}, method="POST"
    )
    assert "error" not in result
    mock_httpx.post.assert_called()


@pytest.mark.asyncio
async def test_audit_log_writes_redacted_line(mock_httpx, monkeypatch, tmp_path):
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("META_ADS_AUDIT_LOG", str(log_path))
    await make_api_request(
        "act_1/campaigns",
        "token",
        {"name": "Secret", "access_token": "SHOULD_NOT_APPEAR", "payload": {"data": ["pii"]}},
        method="POST",
    )
    text = log_path.read_text(encoding="utf-8")
    assert "act_1/campaigns" in text
    assert "SHOULD_NOT_APPEAR" not in text
    assert "<redacted>" in text


@pytest.mark.asyncio
async def test_delete_requires_confirm():
    result = json.loads(await delete_campaign(campaign_id="123", access_token="tok"))
    assert result["error"]["code"] == "confirm_required"


@pytest.mark.asyncio
async def test_delete_with_confirm_calls_api():
    with patch("meta_ads_mcp.core.lifecycle.make_api_request", new_callable=AsyncMock) as mock_api:
        mock_api.return_value = {"success": True}
        result = json.loads(await delete_campaign(campaign_id="123", confirm=True, access_token="tok"))
        assert result["success"] is True
        mock_api.assert_called_once()
        assert mock_api.call_args[1]["method"] == "DELETE" or mock_api.call_args[0][3] == "DELETE" or (
            mock_api.call_args.kwargs.get("method") == "DELETE"
        )


@pytest.mark.asyncio
async def test_delete_product_set_requires_confirm():
    result = json.loads(await delete_product_set(product_set_id="ps1", access_token="tok"))
    assert result["error"]["code"] == "confirm_required"


@pytest.mark.asyncio
async def test_bulk_deleted_requires_confirm():
    result = json.loads(await bulk_update_status(
        object_ids=["1", "2"], status="DELETED", access_token="tok"
    ))
    assert result["error"]["code"] == "confirm_required"


def test_hash_email_is_stable():
    hashed = normalize_and_hash("EMAIL", "  Foo.Bar ")
    assert hashed == normalize_and_hash("EMAIL", "foo.bar")
    assert len(hashed) == 64
    # already hashed values pass through
    assert normalize_and_hash("EMAIL", hashed) == hashed
