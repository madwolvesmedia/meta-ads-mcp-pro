"""Core API functionality for Meta Ads API."""

from typing import Any, Dict, Iterable, List, Optional, Callable
import json
import hmac
import hashlib
import httpx
import asyncio
import functools
import os
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from . import auth
from .auth import needs_authentication, auth_manager, start_callback_server, shutdown_callback_server
from .utils import logger
from . import safety


# Query-string / JSON keys that must never leak to MCP callers.
# access_token is the operator credential; appsecret_proof is derived from
# the app secret + access token via HMAC and is similarly sensitive.
# See GHSA-9gw6-46qc-99vr. Graph paging.next/previous URLs embed these params
# on every list response; sanitize_graph_payload strips them everywhere.
_SENSITIVE_QUERY_PARAMS = frozenset({"access_token", "appsecret_proof"})
_REDACTED = "REDACTED"

_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?P<key>access_token|appsecret_proof)=(?P<val>[^&\s\"'<>\\]*)"
)
_SENSITIVE_JSON_RE = re.compile(
    r'(?i)"(?P<key>access_token|appsecret_proof)"\s*:\s*"(?P<val>[^"]*)"'
)
_SENSITIVE_URLENC_RE = re.compile(
    r"(?i)(?P<key>access_token|appsecret_proof)%3D(?P<val>[^&%\s\"']+)"
)

# Meta (#100) when the app has not been approved for a product API. Catalog /
# DPA tools hit this when catalog_management is missing from the app or token.
_UNAPPROVED_API_SNIPPET = "has not been approved to use this api"
_CATALOG_MANAGEMENT_HINT = (
    "Meta returned (#100) 'This application has not been approved to use this api'. "
    "For product catalogs, product sets, feeds, and catalog ads this means the app is "
    "missing the catalog_management permission (App Review). ads_management alone is "
    "not sufficient. Grant catalog_management to the app and to the system user whose "
    "token you use. Other Graph edges can return the same error when a different "
    "feature permission is missing."
)

# Subcode 1885183: object created while the Meta app is in Development mode.
_DEV_MODE_SUBCODE = 1885183
_DEV_MODE_SNIPPET = "in development mode"
_DEV_MODE_HINT = (
    "Meta returned error subcode 1885183: this object was created by an app that is "
    "in Development mode. Switch the Meta app to Live in the App Dashboard "
    "(App settings → App mode / publish live) before using it to create ads, "
    "audiences, or catalog objects in a real ad account. Development-mode apps "
    "can only be used by people listed as app admins, developers, or testers."
)


def _redact_url(url: str) -> str:
    """Strip sensitive query params (access_token, appsecret_proof) from a URL.

    Used to scrub Graph API URLs (paging.next/previous, error request URLs)
    before they are returned to MCP callers.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        scrubbed = [
            (k, _REDACTED if k in _SENSITIVE_QUERY_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(scrubbed), parts.fragment))
    except Exception:
        # Be conservative: if parsing fails, drop the query string entirely.
        return url.split("?", 1)[0]


def _unique_secrets(secrets: Optional[Iterable[str]] = None) -> List[str]:
    """Longest-first unique secrets, ignoring blanks and tiny strings."""
    seen = set()
    out: List[str] = []
    for secret in secrets or []:
        if not isinstance(secret, str) or len(secret) < 8:
            continue
        if secret not in seen:
            seen.add(secret)
            out.append(secret)
    out.sort(key=len, reverse=True)
    return out


def _redact_sensitive_in_string(value: str, secrets: Optional[Iterable[str]] = None) -> str:
    """Redact tokens from URLs, query strings, JSON text, and known secret values."""
    if not value or not isinstance(value, str):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        value = _redact_url(value)
    if "access_token" in value or "appsecret_proof" in value:
        value = _SENSITIVE_QUERY_RE.sub(lambda m: f"{m.group('key')}=REDACTED", value)
        value = _SENSITIVE_JSON_RE.sub(lambda m: f'"{m.group("key")}": "REDACTED"', value)
        value = _SENSITIVE_URLENC_RE.sub(lambda m: f"{m.group('key')}%3DREDACTED", value)
    for secret in _unique_secrets(secrets):
        if secret in value:
            value = value.replace(secret, _REDACTED)
    return value


def sanitize_graph_payload(data: Any, secrets: Optional[Iterable[str]] = None) -> Any:
    """Recursively strip access_token and appsecret_proof from Graph payloads.

    Covers paging.next/previous URLs, nested field-expansion paging, dict keys,
    JSON-in-string bodies, and leftover copies of known secrets. Idempotent.
    """
    secret_list = _unique_secrets(secrets)
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for key, value in data.items():
            if key in _SENSITIVE_QUERY_PARAMS:
                out[key] = _REDACTED
            else:
                out[key] = sanitize_graph_payload(value, secret_list)
        return out
    if isinstance(data, list):
        return [sanitize_graph_payload(item, secret_list) for item in data]
    if isinstance(data, tuple):
        return [sanitize_graph_payload(item, secret_list) for item in data]
    if isinstance(data, str):
        return _redact_sensitive_in_string(data, secret_list)
    return data


def _is_unapproved_api_error_value(value: Any) -> bool:
    """True when a Graph error value is the unapproved-API (#100) response."""
    if isinstance(value, str):
        return _UNAPPROVED_API_SNIPPET in value.lower()
    if not isinstance(value, dict):
        return False
    message = str(value.get("message", "")).lower()
    if _UNAPPROVED_API_SNIPPET in message:
        return True
    nested = value.get("error")
    if nested is not None and nested is not value:
        return _is_unapproved_api_error_value(nested)
    return False


def _contains_unapproved_api_error(data: Any) -> bool:
    if isinstance(data, dict):
        if _is_unapproved_api_error_value(data):
            return True
        return any(_contains_unapproved_api_error(v) for v in data.values())
    if isinstance(data, list):
        return any(_contains_unapproved_api_error(item) for item in data)
    if isinstance(data, str):
        return _UNAPPROVED_API_SNIPPET in data.lower()
    return False


def _add_hint(payload: Dict[str, Any], hint: str) -> Dict[str, Any]:
    """Attach hint at the payload root and on a matching error object. Idempotent."""
    out = dict(payload)
    existing = out.get("hint")
    if not existing:
        out["hint"] = hint
    elif hint not in str(existing):
        out["hint"] = f"{existing} {hint}"
    err = out.get("error")
    if isinstance(err, dict):
        err = dict(err)
        err_existing = err.get("hint")
        if not err_existing:
            err["hint"] = hint
        elif hint not in str(err_existing):
            err["hint"] = f"{err_existing} {hint}"
        out["error"] = err
    return out


def annotate_unapproved_api_error(payload: Any) -> Any:
    """Attach a catalog_management hint when Meta returns the unapproved-API error.

    Catalog / DPA tools (and graph_api_get against catalog edges) surface
    '(#100) This application has not been approved to use this api' when the
    app or token is missing catalog_management. The hint is added at the payload
    root and on any matching error object. Idempotent.
    """
    if not isinstance(payload, dict) or not _contains_unapproved_api_error(payload):
        return payload
    return _add_hint(payload, _CATALOG_MANAGEMENT_HINT)


def _is_development_mode_error_value(value: Any) -> bool:
    if isinstance(value, str):
        return _DEV_MODE_SNIPPET in value.lower() or "1885183" in value
    if not isinstance(value, dict):
        return False
    if value.get("error_subcode") == _DEV_MODE_SUBCODE or value.get("code") == _DEV_MODE_SUBCODE:
        return True
    blob = " ".join(
        str(value.get(key, ""))
        for key in ("message", "error_user_msg", "error_user_title", "error_subcode")
    ).lower()
    if _DEV_MODE_SNIPPET in blob:
        return True
    nested = value.get("error")
    if nested is not None and nested is not value:
        return _is_development_mode_error_value(nested)
    details = value.get("details")
    if details is not None and details is not value:
        return _is_development_mode_error_value(details)
    return False


def _contains_development_mode_error(data: Any) -> bool:
    if isinstance(data, dict):
        if _is_development_mode_error_value(data):
            return True
        return any(_contains_development_mode_error(v) for v in data.values())
    if isinstance(data, list):
        return any(_contains_development_mode_error(item) for item in data)
    if isinstance(data, str):
        return _DEV_MODE_SNIPPET in data.lower() or "1885183" in data
    return False


def annotate_development_mode_error(payload: Any) -> Any:
    """Attach a Live-app hint when Meta returns subcode 1885183 (development mode)."""
    if not isinstance(payload, dict) or not _contains_development_mode_error(payload):
        return payload
    return _add_hint(payload, _DEV_MODE_HINT)


def annotate_graph_errors(payload: Any) -> Any:
    """Attach known Graph permission / app-mode hints. Idempotent."""
    payload = annotate_unapproved_api_error(payload)
    payload = annotate_development_mode_error(payload)
    return payload


def _safe_api_payload(payload: Any, secrets: Optional[Iterable[str]] = None) -> Any:
    """Sanitize credentials then annotate known Graph permission errors."""
    return annotate_graph_errors(sanitize_graph_payload(payload, secrets))


class McpToolError(Exception):
    """Base class for MCP tool errors that must set isError: true.

    Subclasses should be raised (not returned) from tool handlers.
    meta_api_tool re-raises these so FastMCP sees them and sets
    isError: true in the JSON-RPC response, which triggers the usage
    credit refund in the Next.js proxy.
    """
    pass


def ensure_act_prefix(account_id: str) -> str:
    """Ensure account_id has the 'act_' prefix required by Meta's Graph API."""
    if account_id and not account_id.startswith("act_"):
        return f"act_{account_id}"
    return account_id


# Constants
META_GRAPH_API_VERSION = "v24.0"
META_GRAPH_API_BASE = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}"
USER_AGENT = "meta-ads-mcp/1.1.2"

# Log key environment and configuration at startup
logger.info("Core API module initialized")
logger.info(f"Graph API Version: {META_GRAPH_API_VERSION}")
logger.info(f"META_APP_ID env var present: {'Yes' if os.environ.get('META_APP_ID') else 'No'}")
logger.info(f"META_APP_SECRET env var present (appsecret_proof will be {'enabled' if os.environ.get('META_APP_SECRET') else 'disabled'})")

def _is_account_disabled_error(error_code: Any, error_subcode: Any) -> bool:
    """Return True when a Graph error indicates the ad account / action is
    blocked by Meta policy rather than the token being invalid.

    Currently covers:
      - code 368: "The action attempted has been deemed abusive or is otherwise
        disallowed." Token is still valid; the specific action was rejected.
      - code 190 + subcode 459: user has been checkpointed by Facebook
        security. Token is not expired; the user must clear the checkpoint on
        Facebook before further calls succeed. Telling them to reconnect on
        Pipeboard does not unblock them.

    Other code 190 subcodes (458, 460, 463, 464, 467) are genuine session /
    credential errors — keep the existing invalidate-token behavior for those.
    """
    if error_code == 368:
        return True
    if error_code == 190 and error_subcode == 459:
        return True
    return False


class GraphAPIError(Exception):
    """Exception raised for errors from the Graph API."""
    def __init__(self, error_data: Dict[str, Any]):
        self.error_data = error_data
        self.message = error_data.get('message', 'Unknown Graph API error')
        super().__init__(self.message)

        # Log error details
        logger.error(f"Graph API Error: {self.message}")
        logger.debug(f"Error details: {error_data}")

        code = error_data.get("code")
        subcode = error_data.get("error_subcode")

        # Check if this is an auth error (code 4 is rate limiting, NOT auth)
        if code in [190, 102]:
            if _is_account_disabled_error(code, subcode):
                logger.warning(
                    f"Account/action policy block (code={code}, subcode={subcode}). "
                    f"Token is still valid — NOT invalidating."
                )
            else:
                logger.warning(f"Auth error detected (code: {code}). Invalidating token.")
                auth_manager.invalidate_token()
        elif code == 368:
            logger.warning(
                f"Action disallowed (code=368, subcode={subcode}). "
                f"Token is still valid — NOT invalidating."
            )
        elif code == 4:
            logger.warning(f"Rate limit error detected (code: 4, subcode: {error_data.get('error_subcode', 'N/A')}). Token is still valid — NOT invalidating.")


def _log_meta_rate_limit_headers(headers: dict, endpoint: str) -> None:
    """Log Meta's rate limit headers for observability (X-App-Usage, X-Business-Use-Case-Usage)."""
    app_usage = headers.get("x-app-usage")
    biz_usage = headers.get("x-business-use-case-usage")
    ad_account_usage = headers.get("x-ad-account-usage")

    if app_usage or biz_usage or ad_account_usage:
        usage_data = {}
        if app_usage:
            try:
                usage_data["app_usage"] = json.loads(app_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["app_usage_raw"] = str(app_usage)
        if biz_usage:
            try:
                usage_data["business_use_case_usage"] = json.loads(biz_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["business_use_case_usage_raw"] = str(biz_usage)
        if ad_account_usage:
            try:
                usage_data["ad_account_usage"] = json.loads(ad_account_usage)
            except (json.JSONDecodeError, TypeError):
                usage_data["ad_account_usage_raw"] = str(ad_account_usage)

        # Warn at high usage levels (any field >= 80%)
        is_high = False
        for key, val in usage_data.items():
            if isinstance(val, dict):
                for metric, pct in val.items():
                    if isinstance(pct, (int, float)) and pct >= 80:
                        is_high = True
                        break

        log_fn = logger.warning if is_high else logger.info
        log_fn(f"meta_rate_limit_usage endpoint={endpoint} {json.dumps(usage_data)}")


async def make_api_request(
    endpoint: str,
    access_token: str,
    params: Optional[Dict[str, Any]] = None,
    method: str = "GET",
    mutation: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Make a request to the Meta Graph API.
    
    Args:
        endpoint: API endpoint path (without base URL)
        access_token: Meta API access token
        params: Additional query parameters
        method: HTTP method (GET, POST, PUT, DELETE)
        mutation: Override whether this call is treated as a write. Defaults to
            True for POST/PUT/PATCH/DELETE. Pass False for report-style POSTs
            (e.g. async insights jobs) so META_ADS_READ_ONLY does not block them.
    
    Returns:
        API response as a dictionary
    """
    # Validate access token before proceeding
    if not access_token:
        logger.error("API request attempted with blank access token")
        return _safe_api_payload({
            "error": {
                "message": "Authentication Required",
                "details": "A valid access token is required to access the Meta API",
                "action_required": "Please authenticate first"
            }
        })

    is_mutation = mutation if mutation is not None else method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    if is_mutation:
        if safety.is_read_only():
            return _safe_api_payload(safety.read_only_error())
        budget_error = safety.check_params_budget(params)
        if budget_error:
            return _safe_api_payload(budget_error)
        safety.audit_write(method, endpoint, params)

    url = f"{META_GRAPH_API_BASE}/{endpoint.lstrip('/')}" if endpoint else META_GRAPH_API_BASE
    
    headers = {
        "User-Agent": USER_AGENT,
    }
    
    # Shallow-copy the caller's params: this function injects credentials
    # (access_token, appsecret_proof) and JSON-stringifies dict/list values
    # below. Mutating the caller's dict leaked the access token into tool
    # responses that echo their params back (e.g. update_ad_creative's
    # attempted_updates on error 1815573).
    request_params = dict(params) if params else {}
    request_params["access_token"] = access_token

    # Add appsecret_proof when META_APP_SECRET is configured.
    # Required for system user tokens and recommended by Meta for all
    # server-to-server API calls to verify token authenticity.
    # See: https://developers.facebook.com/docs/graph-api/securing-requests/
    app_secret = os.environ.get("META_APP_SECRET", "")
    if app_secret and access_token:
        request_params["appsecret_proof"] = hmac.new(
            app_secret.encode("utf-8"),
            access_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    secrets = [access_token]
    proof = request_params.get("appsecret_proof")
    if isinstance(proof, str) and proof:
        secrets.append(proof)

    # Logging the request (masking token for security)
    masked_params = {k: "***MASKED***" if k in ("access_token", "appsecret_proof") else v for k, v in request_params.items()}
    logger.debug(f"API Request: {method} {url}")
    logger.debug(f"Request params: {masked_params}")
    
    # Check for app_id in params
    app_id = auth_manager.app_id
    logger.debug(f"Current app_id from auth_manager: {app_id}")
    
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                # For GET, JSON-encode dict/list params (e.g., targeting_spec) to proper strings
                encoded_params = {}
                for key, value in request_params.items():
                    if isinstance(value, (dict, list)):
                        encoded_params[key] = json.dumps(value)
                    else:
                        encoded_params[key] = value
                response = await client.get(url, params=encoded_params, headers=headers, timeout=30.0)
            elif method == "POST":
                # For Meta API, POST requests need data, not JSON
                if 'targeting' in request_params and isinstance(request_params['targeting'], dict):
                    # Convert targeting dict to string for the API
                    request_params['targeting'] = json.dumps(request_params['targeting'])
                
                # Convert lists and dicts to JSON strings    
                for key, value in request_params.items():
                    if isinstance(value, (list, dict)):
                        request_params[key] = json.dumps(value)
                
                logger.debug(f"POST params (prepared): {masked_params}")
                response = await client.post(url, data=request_params, headers=headers, timeout=30.0)
            elif method == "PUT":
                # PUT for updates that Meta requires via PUT (e.g., creative_features_spec).
                # Meta expects access_token as a query param, not in the body.
                query_params = {}
                body_params = {}
                for key, value in request_params.items():
                    if key in ("access_token", "appsecret_proof"):
                        query_params[key] = value
                    elif isinstance(value, (list, dict)):
                        body_params[key] = json.dumps(value)
                    else:
                        body_params[key] = value
                response = await client.put(url, params=query_params, data=body_params, headers=headers, timeout=30.0)
            elif method == "DELETE":
                response = await client.delete(url, params=request_params, headers=headers, timeout=30.0)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            logger.debug(f"API Response status: {response.status_code}")

            # Log Meta rate limit headers for observability
            _log_meta_rate_limit_headers(response.headers, endpoint)

            # Ensure the response is JSON and return it as a dictionary
            try:
                return _safe_api_payload(response.json(), secrets)
            except json.JSONDecodeError:
                # If not JSON, return text content in a structured format
                return _safe_api_payload({
                    "text_response": response.text,
                    "status_code": response.status_code
                }, secrets)
        
        except httpx.HTTPStatusError as e:
            error_info = {}
            try:
                error_info = e.response.json()
            except:
                error_info = {"status_code": e.response.status_code, "text": e.response.text}
            
            logger.error(f"HTTP Error: {e.response.status_code} - {error_info}")

            # Log Meta rate limit headers even on errors
            _log_meta_rate_limit_headers(e.response.headers, endpoint)

            # Check for rate limit errors vs authentication errors.
            # Code 4 is a rate limit (NOT auth) — do NOT invalidate token.
            error_code = None
            error_subcode = None
            is_account_disabled = False
            if "error" in error_info:
                error_obj = error_info.get("error", {})
                if isinstance(error_obj, dict):
                    error_code = error_obj.get("code")
                    error_subcode = error_obj.get("error_subcode")

                if error_code == 4:
                    # Application-level rate limit — token is still valid
                    logger.warning(
                        f"Facebook API rate limit (code=4, subcode={error_subcode}, "
                        f"msg={error_obj.get('error_user_msg', error_obj.get('message', 'N/A'))}). "
                        f"Token is still valid — NOT invalidating."
                    )
                elif _is_account_disabled_error(error_code, error_subcode):
                    # Policy-side block (account or action). Token is still
                    # valid; surface a distinct flag so callers can branch
                    # without treating this as a stale-token reconnect.
                    is_account_disabled = True
                    logger.warning(
                        f"Account/action policy block (code={error_code}, subcode={error_subcode}). "
                        f"Token is still valid — NOT invalidating."
                    )
                elif error_code in [190, 102, 200, 10]:
                    logger.warning(f"Detected Facebook API auth error: {error_code}")
                    if error_code == 200 and "Provide valid app ID" in error_obj.get("message", ""):
                        logger.error("Meta API authentication configuration issue")
                        logger.error(f"Current app_id: {app_id}")
                        return _safe_api_payload({
                            "error": {
                                "message": "Meta API authentication configuration issue. Please check your app credentials.",
                                "original_error": error_obj.get("message"),
                                "code": error_code
                            }
                        }, secrets)
                    auth_manager.invalidate_token()
                elif e.response.status_code in [401, 403]:
                    logger.warning(f"Detected authentication error ({e.response.status_code})")
                    auth_manager.invalidate_token()
            elif e.response.status_code in [401, 403]:
                logger.warning(f"Detected authentication error ({e.response.status_code})")
                auth_manager.invalidate_token()

            # Include full details for technical users. URLs are scrubbed of
            # access_token/appsecret_proof — see GHSA-9gw6-46qc-99vr.
            full_response = {
                "headers": dict(e.response.headers),
                "status_code": e.response.status_code,
                "url": _redact_url(str(e.response.url)),
                "reason": getattr(e.response, "reason_phrase", "Unknown reason"),
                "request_method": e.request.method,
                "request_url": _redact_url(str(e.request.url))
            }

            # Return a properly structured error object
            error_payload: Dict[str, Any] = {
                "message": f"HTTP Error: {e.response.status_code}",
                "details": error_info,
                "full_response": full_response,
            }
            if is_account_disabled:
                error_payload["is_account_disabled"] = True
                if error_code is not None:
                    error_payload["error_code"] = error_code
                if error_subcode is not None:
                    error_payload["error_subcode"] = error_subcode
            return _safe_api_payload({"error": error_payload}, secrets)
        
        except Exception as e:
            logger.error(f"Request Error: {str(e)}")
            return _safe_api_payload({"error": {"message": str(e)}}, secrets)


# Generic wrapper for all Meta API tools
def meta_api_tool(func):
    """Decorator for Meta API tools that handles authentication and error handling."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            # Log function call
            logger.debug(f"Function call: {func.__name__}")
            logger.debug(f"Args: {args}")
            # Log kwargs without sensitive info
            safe_kwargs = {k: ('***TOKEN***' if k == 'access_token' else v) for k, v in kwargs.items()}
            logger.debug(f"Kwargs: {safe_kwargs}")
            
            # Log app ID information
            app_id = auth_manager.app_id
            logger.debug(f"Current app_id: {app_id}")
            logger.debug(f"META_APP_ID env var: {os.environ.get('META_APP_ID')}")
            
            # If access_token is not in kwargs or not kwargs['access_token'], try to get it from auth_manager
            if 'access_token' not in kwargs or not kwargs['access_token']:
                try:
                    access_token = await auth.get_current_access_token()
                    if access_token:
                        kwargs['access_token'] = access_token
                        logger.debug("Using access token from auth_manager")
                    else:
                        logger.warning("No access token available from auth_manager")
                        # Add more details about why token might be missing
                        if auth_manager.app_id == "YOUR_META_APP_ID" or not auth_manager.app_id:
                            logger.error("TOKEN VALIDATION FAILED: No valid app_id configured")
                            logger.error("Please set META_APP_ID environment variable or configure in your code")
                        else:
                            logger.error("Check logs above for detailed token validation failures")
                except Exception as e:
                    logger.error(f"Error getting access token: {str(e)}")
                    # Add stack trace for better debugging
                    import traceback
                    logger.error(f"Stack trace: {traceback.format_exc()}")
            
            # Final validation - if we still don't have a valid token, return authentication required
            if 'access_token' not in kwargs or not kwargs['access_token']:
                logger.warning("No access token available, authentication needed")
                
                # Add more specific troubleshooting information
                auth_url = auth_manager.get_auth_url()
                app_id = auth_manager.app_id

                logger.error("TOKEN VALIDATION SUMMARY:")
                logger.error(f"- Current app_id: '{app_id}'")
                logger.error(f"- Environment META_APP_ID: '{os.environ.get('META_APP_ID', 'Not set')}'")
                logger.error(f"- META_ACCESS_TOKEN set: {'Yes' if os.environ.get('META_ACCESS_TOKEN') else 'No'}")

                if app_id == "YOUR_META_APP_ID" or not app_id:
                    logger.error("ISSUE DETECTED: No valid Meta App ID configured")
                    logger.error("ACTION REQUIRED: Set META_APP_ID environment variable with a valid App ID")

                if os.environ.get("PIPEBOARD_API_TOKEN"):
                    logger.error(
                        "NOTE: PIPEBOARD_API_TOKEN is set but is ignored by this package. "
                        "Use META_ACCESS_TOKEN, or the hosted MCP at "
                        "https://meta-ads.mcp.pipeboard.co/ which does accept it. "
                        "The Pipeboard CLI also still uses it, so leave it set if you use that."
                    )

                return json.dumps({
                    "error": {
                        "message": "Authentication Required",
                        "details": {
                            "description": "You need to authenticate with the Meta API before using this tool",
                            "action_required": "Please authenticate first",
                            "auth_url": auth_url,
                            "configuration_status": {
                                "app_id_configured": bool(app_id) and app_id != "YOUR_META_APP_ID",
                            },
                            "options": [
                                {
                                    "option": "Use the hosted Meta Ads MCP (no Meta app needed)",
                                    "url": "https://meta-ads.mcp.pipeboard.co/",
                                    "how": "Point your MCP client at this URL and authenticate with your Pipeboard API token."
                                },
                                {
                                    "option": "Bring your own Meta access token",
                                    "url": "https://developers.facebook.com/apps/",
                                    "how": "Create your own Meta app, generate an access token, and set META_ACCESS_TOKEN."
                                }
                            ],
                            "troubleshooting": "Check logs for TOKEN VALIDATION FAILED messages",
                            "markdown_link": f"[Click here to authenticate with Meta Ads API]({auth_url})"
                        }
                    }
                }, indent=2)
                
            # Call the original function
            result = await func(*args, **kwargs)
            secrets = [kwargs["access_token"]] if kwargs.get("access_token") else None

            # FastMCP Image (and other non-JSON) responses must pass through.
            if not isinstance(result, (str, dict, list)):
                return result

            # If the result is a string (JSON), try to parse it to check for errors
            if isinstance(result, str):
                try:
                    result_dict = json.loads(result)
                except json.JSONDecodeError:
                    return json.dumps({"data": _redact_sensitive_in_string(result, secrets)}, indent=2)

                result_dict = _safe_api_payload(result_dict, secrets)
                sanitized_str = json.dumps(result_dict, indent=2)
                try:
                    if "error" in result_dict:
                        logger.error(f"Error in API response: {result_dict['error']}")
                        details = result_dict.get("details")
                        error_obj = details.get("error") if isinstance(details, dict) else None
                        if isinstance(error_obj, dict):
                            if error_obj.get("code") == 200 and "Provide valid app ID" in error_obj.get("message", ""):
                                logger.error("Meta API authentication configuration issue")
                                logger.error(f"Current app_id: {app_id}")
                                # Replace the confusing error with a more user-friendly one
                                return json.dumps({
                                    "error": {
                                        "message": "Meta API Configuration Issue",
                                        "details": {
                                            "description": "Your Meta API app is not properly configured",
                                            "action_required": "Check your META_APP_ID environment variable",
                                            "current_app_id": app_id,
                                            "original_error": error_obj.get("message")
                                        }
                                    }
                                }, indent=2)
                            return sanitized_str
                        # Keep catalog / unapproved-API hints as structured JSON
                        # so agents see catalog_management guidance immediately.
                        if result_dict.get("hint"):
                            return sanitized_str
                        # Historical envelope: simple {"error": "..."} strings
                        # were wrapped as {"data": "<json>"} (KeyError on missing
                        # details.error). Preserve that for existing clients.
                        return json.dumps({"data": sanitized_str}, indent=2)
                except Exception:
                    # Not JSON or other parsing error, wrap it in a dictionary
                    return json.dumps({"data": sanitized_str}, indent=2)
                return sanitized_str
            
            # If result is already a dictionary (or list), sanitize then serialize
            return json.dumps(_safe_api_payload(result, secrets), indent=2)
        except McpToolError:
            raise  # Let FastMCP set isError: true and refund the usage credit
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {str(e)}")
            return json.dumps({"error": str(e)}, indent=2)

    return wrapper
