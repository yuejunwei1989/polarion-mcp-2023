"""Project discovery and server introspection tools."""

from __future__ import annotations

from typing import Any

from ..normalize import normalize_project, normalize_enumeration, dedupe, text_value
from ..runtime import Runtime, ok, tool

CAPABILITY_LABELS = {
    "metadata": "GET /metadata (server version, REST limits)",
    "custom_field_config": "Custom field configuration API",
    "fields_metadata": "Fields metadata API",
    "license": "License management API",
    "jobs": "Jobs API",
    "pages_crud": "Pages API",
    "collections": "Collections API",
    "document_search": "Document Lucene search",
    "document_part_move": "Document part move/indent",
    "bulk_backlink": "Bulk backlinked work item creation",
}


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client
    config = runtime.config

    @mcp.tool()
    @tool
    def polarion_server_info() -> dict[str, Any]:
        """Inspect the connected Polarion server: version, REST flavour, auth mode and which
        API capabilities are available. Call this first when a tool unexpectedly returns 404,
        because Polarion 2023 lacks several endpoints that exist in 2024+ releases."""
        caps = runtime.capabilities
        missing = [
            f"{name}: {label}"
            for name, label in CAPABILITY_LABELS.items()
            if not caps.supports(name)
        ]
        data: dict[str, Any] = {
            "server": config.base_url,
            "rest_api": config.rest_url,
            "polarion_version": caps.polarion_version,
            "release": caps.release,
            "flavor": caps.flavor,
            "capabilities_detected_by": caps.detected_by,
            "auth_mode": config.resolved_auth_mode,
            "read_only": config.read_only,
            "default_project": config.default_project,
            "page_size": {
                "default": caps.default_page_size,
                "max": caps.max_page_size,
            },
            "capabilities": caps.to_dict(),
            "unsupported_on_this_release": missing,
        }
        if config.default_project and runtime.startup_error is None:
            try:
                data["project_capabilities"] = client.probe_project(config.default_project)
            except Exception as exc:
                data["project_capabilities_error"] = {
                    "message": str(exc),
                    "status": getattr(exc, "status", None),
                }
        if runtime.startup_error is not None:
            error = runtime.startup_error
            data["startup_error"] = (
                error.to_dict() if hasattr(error, "to_dict") else {"message": str(error)}
            )
            data["ready"] = False
        else:
            data["ready"] = True
        return ok(data)

    @mcp.tool()
    @tool
    def polarion_list_projects(
        query: str | None = None,
        limit: int = 50,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """List accessible Polarion projects.

        Args:
            query: Optional case-insensitive substring matched against project id and name.
            limit: Maximum number of projects to return (default 50).
            page_size: Optional REST page size; capped by the server's maximum.
        """
        limit = max(1, min(int(limit), 500))
        raw = client.get_all(
            "projects",
            params={"fields[projects]": "@basic"},
            page_size=page_size,
            max_items=limit + 1 if not query else config.max_items,
        )
        projects = [normalize_project(item) for item in raw]
        if query:
            needle = query.strip().lower()
            projects = [
                p
                for p in projects
                if needle in p.get("id", "").lower() or needle in str(p.get("name", "")).lower()
            ]
        has_more = len(projects) > limit
        projects = projects[:limit]
        return ok(projects, count=len(projects), has_more=has_more)

    @mcp.tool()
    @tool
    def polarion_get_project(project_id: str | None = None) -> dict[str, Any]:
        """Get one project's details, including work item types when the server exposes them.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
        """
        pid = runtime.project(project_id)
        raw = client.get(f"projects/{pid}", params={"fields[projects]": "@all"})
        project = normalize_project(raw.get("data", raw) if isinstance(raw, dict) else raw)

        if not project.get("work_item_types"):
            types = _discover_work_item_types(runtime, pid)
            if types:
                project["work_item_types"] = types

        project["capabilities"] = client.probe_project(pid)
        return ok(project)

    @mcp.tool()
    @tool
    def polarion_list_work_item_types(project_id: str | None = None) -> dict[str, Any]:
        """List the Work Item types configured for a project.

        Polarion 2023 has no fields-metadata endpoint, so this resolves types from the
        project's enumerations and falls back to sampling existing work items.
        """
        pid = runtime.project(project_id)
        types = _discover_work_item_types(runtime, pid)
        if not types:
            return ok(
                [],
                project=pid,
                note=(
                    "No work item types could be discovered. The project may have no work items "
                    "yet, or the credential cannot read project configuration."
                ),
            )
        return ok(types, project=pid, count=len(types))


def _discover_work_item_types(runtime: Runtime, project_id: str) -> list[str]:
    """Best-effort work item type discovery for 2023 servers."""
    client = runtime.client

    # 1) Project relationship (available on some 2310 configurations).
    try:
        raw = client.get(f"projects/{project_id}", params={"fields[projects]": "@all"})
        project = raw.get("data", raw) if isinstance(raw, dict) else raw
        relationships = (project or {}).get("relationships") or {}
        node = (relationships.get("workItemTypes") or {}).get("data")
        if isinstance(node, list):
            found = [str(item.get("id")) for item in node if isinstance(item, dict) and item.get("id")]
            if found:
                return dedupe(found)
    except Exception:
        pass

    # 2) Enumeration named workItemType (2310 exposes enumeration endpoints).
    for enum_id in ("workItemType", "workitem-type", "workItemTypes"):
        try:
            raw = client.get(f"projects/{project_id}/enumerations/{enum_id}")
            enumeration = normalize_enumeration(raw.get("data", raw))
            options = enumeration.get("options") or []
            found = [str(option["id"]) for option in options if option.get("id")]
            if found:
                return dedupe(found)
        except Exception:
            continue

    # 3) Scan the enumeration index for a work item type enumeration.
    try:
        raw = client.get(f"projects/{project_id}/enumerations", params={"page[size]": 100})
        data = raw.get("data") if isinstance(raw, dict) else raw
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                attributes = entry.get("attributes") or {}
                label = " ".join(
                    str(attributes.get(key) or "")
                    for key in ("name", "id", "type")
                ).lower()
                if "work item type" in label or "workitemtype" in label:
                    enum_id = entry.get("id") or attributes.get("id")
                    if enum_id:
                        detail = client.get(f"projects/{project_id}/enumerations/{enum_id}")
                        enumeration = normalize_enumeration(detail.get("data", detail))
                        found = [
                            str(option["id"])
                            for option in enumeration.get("options") or []
                            if option.get("id")
                        ]
                        if found:
                            return dedupe(found)
    except Exception:
        pass

    # 4) Sample existing work items.
    try:
        sampled = client.get_all(
            f"projects/{project_id}/workitems",
            params={"fields[workitems]": "type"},
            page_size=100,
            max_items=300,
        )
        found = []
        for item in sampled:
            attributes = item.get("attributes") or {}
            value = text_value(attributes.get("type"))
            if not value:
                rel = (item.get("relationships") or {}).get("type", {}).get("data")
                value = rel.get("id") if isinstance(rel, dict) else None
            if value:
                found.append(str(value))
        return dedupe(found)
    except Exception:
        return []
