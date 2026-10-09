"""Small shared helpers for MCP tool modules."""

import json
from typing import Any, Dict, List, Optional, Union

from .api import annotate_graph_errors, sanitize_graph_payload


def dump(data: Any) -> str:
    """Serialize a tool response as indented JSON with credentials stripped."""
    return json.dumps(
        annotate_graph_errors(sanitize_graph_payload(data)),
        indent=2,
    )


def error(message: str, **extra: Any) -> str:
    """Serialize a structured error payload."""
    payload: Dict[str, Any] = {"error": message}
    payload.update(extra)
    return dump(payload)


def parse_jsonish(value: Any) -> Any:
    """Parse a JSON object/array if an LLM client passed it as a string."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def as_id_list(value: Optional[Union[str, List[str]]]) -> List[str]:
    """Normalize a single ID or list of IDs into a list of strings."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parsed = parse_jsonish(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]
