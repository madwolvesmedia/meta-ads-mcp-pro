"""Safety guards for Meta Ads MCP write operations.

Env vars
--------
META_ADS_READ_ONLY
    When set to a truthy value (1, true, yes, on), every mutating Graph call
    (POST/PUT/PATCH/DELETE, and any call marked mutation=True) is rejected.

META_ADS_MAX_DAILY_BUDGET
    Integer cap in the same units as Meta daily_budget (account currency cents
    for USD/EUR/etc.). Create/update calls that set daily_budget above this
    value are rejected.

META_ADS_AUDIT_LOG
    Optional filesystem path. When set, mutating calls are appended as JSON
    lines (credentials and customer-list payloads redacted).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .utils import logger, restrict_permissions

_TRUTHY = {"1", "true", "yes", "on"}

# Keys whose values must never be written to the audit log.
_REDACT_KEYS = frozenset({
    "access_token",
    "appsecret_proof",
    "payload",
    "session",
    "data",
    "users",
    "file",
    "video_file_chunk",
    "bytes",
    "image_file",
    "source",
})


def env_flag(name: str) -> bool:
    """Return True if the named env var is a truthy flag."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_read_only() -> bool:
    return env_flag("META_ADS_READ_ONLY")


def read_only_error() -> Dict[str, Any]:
    return {
        "error": {
            "message": "Write blocked: META_ADS_READ_ONLY is enabled",
            "details": "This server is running in read-only mode. Unset META_ADS_READ_ONLY to allow mutating Graph API calls.",
            "code": "read_only",
        }
    }


def get_max_daily_budget_cents() -> Optional[int]:
    raw = os.environ.get("META_ADS_MAX_DAILY_BUDGET", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("META_ADS_MAX_DAILY_BUDGET is not an integer: %r", raw)
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def budget_cap_error(amount_cents: int, cap_cents: int) -> Dict[str, Any]:
    return {
        "error": {
            "message": f"daily_budget {amount_cents} exceeds META_ADS_MAX_DAILY_BUDGET ({cap_cents})",
            "details": "Raise META_ADS_MAX_DAILY_BUDGET (cents) or lower the requested daily_budget.",
            "code": "budget_cap",
            "requested_daily_budget": amount_cents,
            "max_daily_budget": cap_cents,
        }
    }


def check_daily_budget(amount: Any) -> Optional[Dict[str, Any]]:
    """Return an error payload if amount exceeds the configured daily cap."""
    cap = get_max_daily_budget_cents()
    if cap is None:
        return None
    cents = _to_int(amount)
    if cents is None:
        return None
    if cents > cap:
        return budget_cap_error(cents, cap)
    return None


def check_params_budget(params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not params:
        return None
    if "daily_budget" in params:
        return check_daily_budget(params.get("daily_budget"))
    return None


def confirm_error(operation: str) -> str:
    return json.dumps({
        "error": {
            "message": f"Refusing {operation} without confirm=true",
            "details": "Destructive operations require an explicit confirm=true parameter.",
            "code": "confirm_required",
            "operation": operation,
        }
    }, indent=2)


def require_confirm(confirm: Any, operation: str) -> Optional[str]:
    """Return a JSON error string when confirm is not truthy."""
    if confirm is True or (isinstance(confirm, str) and confirm.strip().lower() in _TRUTHY):
        return None
    if confirm == 1:
        return None
    return confirm_error(operation)


def _redact_for_audit(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not params:
        return {}
    redacted: Dict[str, Any] = {}
    for key, value in params.items():
        if key in _REDACT_KEYS:
            redacted[key] = "<redacted>"
        elif isinstance(value, (dict, list)):
            # Keep shape only — catalog filters are useful, customer lists are not.
            redacted[key] = f"<{type(value).__name__} len={len(value)}>"
        else:
            redacted[key] = value
    return redacted


def audit_write(method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> None:
    """Append one JSON line to META_ADS_AUDIT_LOG when configured."""
    path = os.environ.get("META_ADS_AUDIT_LOG", "").strip()
    if not path:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "endpoint": endpoint,
        "params": _redact_for_audit(params),
    }
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        try:
            from pathlib import Path
            restrict_permissions(Path(path), 0o600)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Failed to write META_ADS_AUDIT_LOG: %s", exc)
