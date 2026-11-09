"""Error types and JSON:API error parsing."""

from __future__ import annotations

import json
from typing import Any


class PolarionError(Exception):
    """A non-2xx response from the Polarion REST API."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        detail: str | None = None,
        method: str | None = None,
        path: str | None = None,
        payload: Any = None,
    ) -> None:
        self.status = status
        self.message = message
        self.detail = detail
        self.method = method
        self.path = path
        self.payload = payload
        super().__init__(self.render())

    def render(self) -> str:
        parts = [f"HTTP {self.status}: {self.message}"]
        if self.detail and self.detail != self.message:
            parts.append(self.detail)
        if self.method and self.path:
            parts.append(f"({self.method} {self.path})")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "message": self.message,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.method:
            out["method"] = self.method
        if self.path:
            out["path"] = self.path
        if self.status == 401:
            out["hint"] = (
                "Authentication failed. For Polarion 2023 check POLARION_USERNAME/POLARION_PASSWORD "
                "or a valid PAT in POLARION_TOKEN, and that the REST API is enabled."
            )
        elif self.status == 403:
            out["hint"] = (
                "The authenticated user lacks permission for this operation. Polarion enforces "
                "project-level and global REST permissions of the credential owner."
            )
        elif self.status == 404:
            out["hint"] = (
                "Resource not found. On Polarion 2023 some endpoints introduced in 2404+ "
                "(metadata, custom field configuration, fields metadata, license, jobs, "
                "collections) do not exist. Run polarion_server_info to inspect capabilities."
            )
        elif self.status == 405:
            out["hint"] = "HTTP method not supported by this Polarion version at this endpoint."
        elif self.status == 422:
            out["hint"] = (
                "The request was rejected by validation. Check JSON:API body shape, field ids, "
                "enumeration values and required relationships."
            )
        return out


def parse_error_body(status: int, body: bytes | str, content_type: str = "") -> PolarionError:
    """Parse Polarion's JSON:API error payload, falling back to raw text."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else (body or "")
    text = text.strip()

    message = f"Request failed with status {status}"
    detail: str | None = None
    payload: Any = None

    if text:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0] if isinstance(errors[0], dict) else {}
                message = str(first.get("title") or first.get("status") or message)
                detail = first.get("detail") or first.get("message")
                if len(errors) > 1:
                    extra = "; ".join(
                        str(e.get("detail") or e.get("title"))
                        for e in errors[1:]
                        if isinstance(e, dict)
                    )
                    if extra:
                        detail = f"{detail} (and {len(errors) - 1} more: {extra})" if detail else extra
            elif isinstance(payload.get("error"), str):
                message = payload["error"]
            elif isinstance(payload.get("message"), str):
                message = payload["message"]
            elif isinstance(payload.get("title"), str):
                message = payload["title"]
        elif text and not text.lstrip().startswith("<"):
            message = text[:500]

    if not detail and text.startswith("<"):
        detail = "Server returned HTML instead of JSON. This usually means the URL points to the " \
                 "Polarion web UI rather than the REST API, or a reverse proxy intercepted the request."

    return PolarionError(status, message, detail=detail, payload=payload)


class ReadOnlyError(Exception):
    """Raised when a write tool is called in read-only mode."""
