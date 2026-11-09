"""Shared runtime helpers for tool modules."""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from .capabilities import Capabilities
from .client import PolarionClient
from .config import Config
from .errors import PolarionError, ReadOnlyError

LOG = logging.getLogger("polarion_mcp.tools")


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def ok(data: Any = None, **meta: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True}
    if data is not None:
        payload["data"] = data
    if meta:
        payload["meta"] = meta
    return payload


def tool(func: Callable[..., Any]) -> Callable[..., dict[str, Any]]:
    """Catch domain errors and return a structured, actionable result."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except ReadOnlyError as exc:
            return {
                "ok": False,
                "error": {
                    "kind": "read_only",
                    "message": str(exc),
                    "hint": "Unset POLARION_READ_ONLY to enable write tools.",
                },
            }
        except PolarionError as exc:
            LOG.warning("%s failed: %s", func.__name__, exc.render())
            return {"ok": False, "error": exc.to_dict()}
        except ValueError as exc:
            return {"ok": False, "error": {"kind": "invalid_argument", "message": str(exc)}}
        except Exception as exc:  # pragma: no cover - unexpected
            LOG.exception("Unexpected error in %s", func.__name__)
            return {
                "ok": False,
                "error": {"kind": "internal", "message": f"{type(exc).__name__}: {exc}"},
            }

    return wrapper


@dataclass
class Runtime:
    config: Config
    client: PolarionClient
    capabilities: Capabilities
    startup_error: Any = None

    def project(self, project_id: str | None) -> str:
        resolved = (project_id or self.config.default_project or "").strip()
        if not resolved:
            raise ValueError(
                "project_id is required. Pass it explicitly or set POLARION_DEFAULT_PROJECT."
            )
        return resolved

    def ensure_write(self) -> None:
        if self.config.read_only:
            raise ReadOnlyError("The server is running in read-only mode (POLARION_READ_ONLY=1).")

    def require(self, feature: str, version: str) -> None:
        if not self.capabilities.supports(feature):
            raise ValueError(
                f"This operation requires the '{feature}' REST capability, which is not available "
                f"on the connected Polarion release (detected: {self.capabilities.polarion_version or self.capabilities.release}). "
                f"It was introduced in Polarion {version}."
            )

    def dry_run(self, method: str, path: str, body: Any) -> dict[str, Any]:
        return ok(
            {
                "dry_run": True,
                "request": {"method": method, "path": path, "body": body},
            }
        )


def work_item_body(
    attributes: dict[str, Any],
    *,
    item_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build a JSON:API Work Item document.

    Polarion 2023 accepts a list for POST and requires the project relationship
    in the payload when posting to the global /workitems collection.
    """
    payload: dict[str, Any] = {"type": "workitems", "attributes": attributes}
    if item_id:
        payload["id"] = item_id
    relationships: dict[str, Any] = {}
    if project_id:
        relationships["project"] = {"data": {"type": "projects", "id": project_id}}
    if relationships:
        payload["relationships"] = relationships
    return {"data": payload}


def work_item_list_body(
    attributes: dict[str, Any],
    *,
    project_id: str,
    work_item_type: str | None = None,
) -> dict[str, Any]:
    attrs = dict(attributes)
    if work_item_type:
        attrs["type"] = work_item_type
    return {
        "data": [
            {
                "type": "workitems",
                "attributes": attrs,
                "relationships": {"project": {"data": {"type": "projects", "id": project_id}}},
            }
        ]
    }


def first_item(payload: Any) -> dict[str, Any] | None:
    """Normalise Polarion's single-or-list response shapes."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data
    return None


def items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def require_id(value: str | None, name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def chunked(values: list[str], size: int = 50) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]
