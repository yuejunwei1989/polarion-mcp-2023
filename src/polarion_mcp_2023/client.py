"""HTTP client for the Polarion REST API, tuned for 2023-era servers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests

from .capabilities import Capabilities, FLAVOR_BASELINE, parse_release
from .config import Config
from .errors import PolarionError, parse_error_body

LOG = logging.getLogger("polarion_mcp.client")

JSONAPI = "application/vnd.api+json"
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class RawResponse:
    content: bytes
    content_type: str
    filename: str | None
    status: int


class PolarionClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": f"{JSONAPI}, application/json;q=0.9",
                "User-Agent": config.user_agent,
            }
        )
        self.session.verify = config.verify_ssl
        if not config.verify_ssl:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:  # pragma: no cover - urllib3 always present with requests
                pass

        mode = config.resolved_auth_mode
        if mode == "token":
            self.session.headers["Authorization"] = f"Bearer {config.token}"
        elif mode == "basic":
            self.session.auth = (config.username or "", config.password or "")

        self._capabilities: Capabilities | None = None
        self._project_caps: dict[str, dict[str, bool | None]] = {}
        self._connectivity_error: PolarionError | None = None

    # ------------------------------------------------------------------ core

    @property
    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            self._capabilities = Capabilities.for_release(
                None,
                flavor="2023" if self.config.api_flavor != "2025" else "2025",
                detected_by="lazy",
                max_page_size=self.config.max_page_size,
                default_page_size=self.config.default_page_size,
            )
        return self._capabilities

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.config.rest_url}/{path.lstrip('/')}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        expect_json: bool = True,
        raw: bool = False,
    ) -> Any:
        url = self._url(path)
        hdrs = dict(headers or {})
        if json_body is not None:
            hdrs.setdefault("Content-Type", JSONAPI)

        attempt = 0
        while True:
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=hdrs,
                    timeout=(min(10.0, self.config.timeout), self.config.timeout),
                    allow_redirects=True,
                )
            except requests.Timeout as exc:
                if attempt < self.config.max_retries:
                    attempt += 1
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise PolarionError(
                    0,
                    "Request to Polarion timed out",
                    detail=str(exc),
                    method=method,
                    path=path,
                ) from exc
            except requests.RequestException as exc:
                if attempt < self.config.max_retries:
                    attempt += 1
                    time.sleep(0.5 * (2 ** (attempt - 1)))
                    continue
                raise PolarionError(
                    0,
                    "Could not reach Polarion",
                    detail=f"{exc}. Check POLARION_URL, network access and TLS settings.",
                    method=method,
                    path=path,
                ) from exc

            if response.status_code in RETRY_STATUS and attempt < self.config.max_retries:
                attempt += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 0.5 * (2 ** (attempt - 1))
                except ValueError:
                    delay = 0.5 * (2 ** (attempt - 1))
                LOG.warning(
                    "Polarion returned %s for %s %s; retrying in %.1fs",
                    response.status_code,
                    method,
                    path,
                    delay,
                )
                time.sleep(min(delay, 10.0))
                continue
            break

        if response.status_code >= 400:
            error = parse_error_body(
                response.status_code,
                response.content,
                response.headers.get("Content-Type", ""),
            )
            error.method = method
            error.path = path
            raise error

        if raw:
            disposition = response.headers.get("Content-Disposition", "")
            filename = None
            if "filename=" in disposition:
                filename = disposition.split("filename=", 1)[1].strip().strip('"')
            return RawResponse(
                content=response.content,
                content_type=response.headers.get("Content-Type", ""),
                filename=filename,
                status=response.status_code,
            )

        if response.status_code in (204, 205) or not response.content:
            return None

        if not expect_json:
            return response.text

        content_type = response.headers.get("Content-Type", "")
        try:
            return response.json()
        except ValueError as exc:
            if "html" in content_type.lower():
                raise PolarionError(
                    response.status_code,
                    "Polarion returned HTML instead of JSON",
                    detail=(
                        "The URL likely points at the Polarion web UI or a reverse proxy "
                        "login page. Expected the REST root at <server>/polarion/rest/v1."
                    ),
                    method=method,
                    path=path,
                ) from exc
            raise PolarionError(
                response.status_code,
                "Polarion returned a non-JSON response",
                detail=response.text[:300],
                method=method,
                path=path,
            ) from exc

    def get(self, path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("GET", path, params=params, **kwargs)

    def post(self, path: str, json_body: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, json_body=json_body, **kwargs)

    def patch(self, path: str, json_body: Any = None, **kwargs: Any) -> Any:
        return self.request("PATCH", path, json_body=json_body, **kwargs)

    def delete(self, path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("DELETE", path, params=params, **kwargs)

    # ------------------------------------------------------------- discovery

    @staticmethod
    def _deep_find(obj: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if isinstance(value, (str, int, float)) and str(value).strip():
                    return value
            for value in obj.values():
                found = PolarionClient._deep_find(value, keys)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = PolarionClient._deep_find(value, keys)
                if found is not None:
                    return found
        return None

    def _fetch_metadata(self) -> dict[str, Any] | None:
        """Return metadata on 2404+, or ``None`` when the endpoint is absent."""
        try:
            payload = self.get("metadata")
        except PolarionError as exc:
            if exc.status in {400, 404, 405, 501}:
                return None
            if exc.status in {401, 403}:
                raise
            return None

        if not isinstance(payload, dict):
            return None
        attributes = payload.get("data", {}).get("attributes", payload)
        version = self._deep_find(attributes, ("polarionVersion", "version", "productVersion"))
        build = self._deep_find(attributes, ("build", "buildNumber"))
        properties = None
        for key in ("restApiProperties", "restProperties", "properties"):
            if isinstance(attributes.get(key), dict):
                properties = attributes[key]
                break
        max_page = self._deep_find(properties or {}, ("maxPageSize", "maximumPageSize"))
        default_page = self._deep_find(properties or {}, ("defaultPageSize", "pageSize"))
        return {
            "version": str(version) if version is not None else None,
            "build": str(build) if build is not None else None,
            "max_page_size": int(max_page) if isinstance(max_page, int) else None,
            "default_page_size": int(default_page) if isinstance(default_page, int) else None,
        }

    def probe(self) -> Capabilities:
        """Detect the server flavour without assuming a 2025 endpoint exists."""
        flavor = self.config.api_flavor
        metadata: dict[str, Any] | None = None
        detected_by = "config"

        if flavor == "2025":
            metadata = self._fetch_metadata()
            if metadata is None:
                raise PolarionError(
                    404,
                    "POLARION_API_FLAVOR=2025 but the /metadata endpoint is unavailable",
                    detail="Use POLARION_API_FLAVOR=2023 or auto for Polarion 2023 servers.",
                )
            detected_by = "metadata"
        elif flavor == "2023":
            detected_by = "config"
        else:
            metadata = self._fetch_metadata()
            if metadata is not None:
                flavor = "2025"
                detected_by = "metadata"
            else:
                flavor = "2023"
                detected_by = "metadata-absent"

        # Verify the REST root and credentials with the one endpoint every
        # supported release has. This also catches HTML login pages.
        try:
            self.get("projects", params={"fields[projects]": "@basic", "page[size]": 1})
        except PolarionError as exc:
            if exc.status == 403:
                # Authentication succeeded but the credential may only see specific
                # projects. The server stays usable when POLARION_DEFAULT_PROJECT is set.
                LOG.warning(
                    "Authenticated, but listing projects is forbidden: %s. "
                    "Set POLARION_DEFAULT_PROJECT to work with a specific project.",
                    exc.render(),
                )
            else:
                raise
        self._connectivity_error = None

        release = parse_release(metadata.get("version") if metadata else None)
        if release is None:
            release = FLAVOR_BASELINE.get(flavor, 2310)

        max_page = (metadata or {}).get("max_page_size") or self.config.max_page_size
        default_page = (metadata or {}).get("default_page_size") or self.config.default_page_size

        self._capabilities = Capabilities.for_release(
            release,
            flavor=flavor,
            polarion_version=(metadata or {}).get("version"),
            build=(metadata or {}).get("build"),
            detected_by=detected_by,
            max_page_size=min(int(max_page), 1000),
            default_page_size=min(int(default_page), int(max_page)),
        )
        LOG.info(
            "Connected to Polarion %s (%s), flavour=%s, capabilities detected by %s",
            self._capabilities.polarion_version or "unknown",
            release,
            flavor,
            detected_by,
        )
        return self._capabilities

    def probe_project(self, project_id: str) -> dict[str, bool | None]:
        """Check optional project-scoped collections; returns tri-state flags."""
        cached = self._project_caps.get(project_id)
        if cached is not None:
            return cached

        checks = {
            "documents": f"projects/{project_id}/spaces",
            "test_runs": f"projects/{project_id}/testruns",
            "enumerations": f"projects/{project_id}/enumerations",
            "plans": f"projects/{project_id}/plans",
        }
        result: dict[str, bool | None] = {}
        for name, path in checks.items():
            try:
                self.get(path, params={"page[size]": 1})
                result[name] = True
            except PolarionError as exc:
                if exc.status in {404, 405, 501}:
                    result[name] = False
                elif exc.status in {401, 403}:
                    result[name] = None
                else:
                    result[name] = None
        self._project_caps[project_id] = result
        return result

    # -------------------------------------------------------------- paginate

    def iter_collection(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int | None = None,
        max_items: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        hard_cap = self.capabilities.max_page_size or 100
        size = min(page_size or self.config.default_page_size, hard_cap)
        size = max(1, size)
        limit = max_items if max_items is not None else self.config.max_items
        limit = max(1, min(limit, self.config.max_items))

        page = 1
        fetched = 0
        while True:
            query = dict(params or {})
            query["page[size]"] = size
            query["page[number]"] = page
            payload = self.get(path, params=query)
            data = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(data, list):
                LOG.warning("Expected a collection at %s, got %r", path, type(data))
                return

            for item in data:
                if isinstance(item, dict):
                    yield item
                    fetched += 1
                    if fetched >= limit:
                        return

            if len(data) < size:
                return
            total = None
            if isinstance(payload, dict):
                total = payload.get("meta", {}).get("totalCount")
            if isinstance(total, int) and fetched >= total:
                return
            page += 1
            if page > 1000:  # defensive guard against a misbehaving proxy
                LOG.warning("Stopping pagination at page 1000 for %s", path)
                return

    def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_size: int | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        return list(self.iter_collection(path, params, page_size=page_size, max_items=max_items))

    # --------------------------------------------------------------- helpers

    def work_item_url(self, project_id: str, work_item_id: str) -> str:
        return f"{self.config.base_url}/#/project/{project_id}/workitem?id={work_item_id}"

    def close(self) -> None:
        self.session.close()
