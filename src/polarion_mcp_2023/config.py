"""Configuration for the Polarion 2023-compatible MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse


class ConfigError(ValueError):
    """Raised when the server configuration is invalid."""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def normalize_base_url(raw: str) -> str:
    """Return the Polarion web root, e.g. ``https://host/polarion``.

    Accepts any of:
      https://host
      https://host/
      https://host/polarion
      https://host/polarion/rest/v1
    """
    raw = (raw or "").strip()
    if not raw:
        raise ConfigError("POLARION_URL is required (e.g. https://polarion.example.com)")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"POLARION_URL must be an absolute http(s) URL, got {raw!r}")

    path = parsed.path.rstrip("/")
    for suffix in ("/rest/v1", "/rest"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path.endswith("/polarion"):
        # Default Polarion context root. Users with a custom context path can
        # pass it explicitly, e.g. https://host/mycompany/polarion
        path = f"{path}/polarion" if path else "/polarion"

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


@dataclass(frozen=True)
class Config:
    base_url: str
    rest_url: str
    username: str | None
    password: str | None
    token: str | None
    auth_mode: str  # auto | basic | token
    default_project: str | None
    verify_ssl: bool
    timeout: float
    max_retries: int
    default_page_size: int
    max_page_size: int
    max_items: int
    api_flavor: str  # auto | 2023 | 2025
    read_only: bool
    log_level: str
    user_agent: str = "polarion-mcp-2023/1.0"
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def resolved_auth_mode(self) -> str:
        if self.auth_mode != "auto":
            return self.auth_mode
        if self.token:
            return "token"
        if self.username and self.password:
            return "basic"
        return "none"

    @classmethod
    def from_env(cls) -> "Config":
        base = normalize_base_url(os.getenv("POLARION_URL", ""))

        username = os.getenv("POLARION_USERNAME") or None
        password = os.getenv("POLARION_PASSWORD") or None
        token = os.getenv("POLARION_TOKEN") or None
        auth_mode = (os.getenv("POLARION_AUTH_MODE") or "auto").strip().lower()
        if auth_mode not in {"auto", "basic", "token"}:
            raise ConfigError("POLARION_AUTH_MODE must be one of: auto, basic, token")

        if auth_mode == "basic" and not (username and password):
            raise ConfigError("POLARION_AUTH_MODE=basic requires POLARION_USERNAME and POLARION_PASSWORD")
        if auth_mode == "token" and not token:
            raise ConfigError("POLARION_AUTH_MODE=token requires POLARION_TOKEN")
        if auth_mode == "auto" and not (token or (username and password)):
            raise ConfigError(
                "No credentials configured. Set POLARION_TOKEN (PAT) or "
                "POLARION_USERNAME + POLARION_PASSWORD (Basic auth)."
            )

        api_flavor = (os.getenv("POLARION_API_FLAVOR") or "auto").strip().lower()
        if api_flavor not in {"auto", "2023", "2025"}:
            raise ConfigError("POLARION_API_FLAVOR must be one of: auto, 2023, 2025")

        page_size = _env_int("POLARION_PAGE_SIZE", 100)
        max_page_size = _env_int("POLARION_MAX_PAGE_SIZE", 100)
        page_size = max(1, min(page_size, max_page_size))

        return cls(
            base_url=base,
            rest_url=f"{base}/rest/v1",
            username=username,
            password=password,
            token=token,
            auth_mode=auth_mode,
            default_project=os.getenv("POLARION_DEFAULT_PROJECT") or None,
            verify_ssl=_env_bool("POLARION_VERIFY_SSL", True),
            timeout=float(_env_int("POLARION_TIMEOUT", 30)),
            max_retries=_env_int("POLARION_MAX_RETRIES", 2),
            default_page_size=page_size,
            max_page_size=max_page_size,
            max_items=_env_int("POLARION_MAX_ITEMS", 1000),
            api_flavor=api_flavor,
            read_only=_env_bool("POLARION_READ_ONLY", False),
            log_level=(os.getenv("POLARION_LOG_LEVEL") or "INFO").upper(),
        )
