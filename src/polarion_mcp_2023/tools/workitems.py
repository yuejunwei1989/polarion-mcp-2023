"""Work Item tools: search, read, create, update, delete, revisions."""

from __future__ import annotations

from typing import Any

from ..errors import PolarionError
from ..normalize import normalize_work_item, text_value
from ..runtime import (
    Runtime,
    first_item,
    items,
    ok,
    require_id,
    tool,
    work_item_list_body,
)

SEARCH_TIPS = (
    "Polarion uses Lucene syntax. Examples: 'type:requirement AND status:open', "
    "'title:\"power supply\"', 'severity:(critical OR major)', "
    "'assignee:alice AND NOT status:closed', 'updated:[20240101 TO 20241231]'."
)


def _infer_project(runtime: Runtime, work_item_id: str, project_id: str | None) -> str:
    if project_id:
        return runtime.project(project_id)
    if runtime.config.default_project:
        return runtime.config.default_project
    if "-" in work_item_id:
        candidate = work_item_id.rsplit("-", 1)[0]
        if candidate:
            return candidate
    raise ValueError(
        "Cannot determine the project. Pass project_id explicitly or set "
        "POLARION_DEFAULT_PROJECT."
    )


def _item_path(project_id: str, work_item_id: str, suffix: str = "") -> str:
    base = f"projects/{project_id}/workitems/{work_item_id}"
    return f"{base}/{suffix}" if suffix else base


def _normalize_links(payload: Any, self_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entry in items(payload):
        attributes = entry.get("attributes") or {}
        relationships = entry.get("relationships") or {}
        source = ((relationships.get("source") or {}).get("data") or {}).get("id")
        target = ((relationships.get("target") or {}).get("data") or {}).get("id")
        if source == self_id:
            direction, other = "outgoing", target
        elif target == self_id:
            direction, other = "incoming", source
        else:
            direction, other = "unknown", target or source
        result.append(
            {
                "link_id": entry.get("id"),
                "role": attributes.get("role") or text_value(attributes.get("role")),
                "suspect": attributes.get("suspect"),
                "direction": direction,
                "other_work_item": other,
            }
        )
    return result


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client

    @mcp.tool()
    @tool
    def polarion_search_work_items(
        project_id: str | None = None,
        query: str | None = None,
        limit: int = 25,
        page_size: int | None = None,
        sort: str | None = None,
        fields: str | None = None,
        description_limit: int = 300,
        include_custom_fields: bool = True,
    ) -> dict[str, Any]:
        """Search Work Items in a project with a Polarion Lucene query.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            query: Lucene query, e.g. 'type:requirement AND status:open'. Empty returns all.
            limit: Maximum work items to return (default 25, max 500).
            page_size: REST page size (Polarion 2023 caps this at 100 by default).
            sort: Sort expression, e.g. 'updated' or '-created'. Prefix '-' for descending.
            fields: Optional sparse-field expression, e.g. '@basic' or 'title,status'.
            description_limit: Truncate each description to this many characters.
            include_custom_fields: Include configured custom fields found on the items.
        """
        pid = runtime.project(project_id)
        limit = max(1, min(int(limit), 500))
        params: dict[str, Any] = {"fields[workitems]": fields or "@all"}
        if query and query.strip():
            params["query"] = query.strip()
        if sort:
            params["sort"] = sort

        raw = client.get_all(
            f"projects/{pid}/workitems",
            params=params,
            page_size=page_size,
            max_items=limit + 1,
        )
        normalized = [
            normalize_work_item(
                item,
                project_id=pid,
                description_limit=description_limit,
                include_custom_fields=include_custom_fields,
            )
            for item in raw
        ]
        has_more = len(normalized) > limit
        normalized = normalized[:limit]
        return ok(
            normalized,
            project=pid,
            count=len(normalized),
            has_more=has_more,
            query=query,
            query_tips=SEARCH_TIPS if not query or not normalized else None,
        )

    @mcp.tool()
    @tool
    def polarion_get_work_item(
        work_item_id: str,
        project_id: str | None = None,
        include_links: bool = True,
        description_limit: int = 8000,
        include_custom_fields: bool = True,
    ) -> dict[str, Any]:
        """Get one Work Item by id with its fields, custom fields and traceability links.

        Args:
            work_item_id: Work Item id, e.g. 'PROJ-123'.
            project_id: Project id. Inferred from the Work Item id when omitted.
            include_links: Fetch linked and backlinked work items with their link roles.
            description_limit: Truncate the description to this many characters.
            include_custom_fields: Include configured custom fields.
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        raw = client.get(_item_path(pid, item_id), params={"fields[workitems]": "@all"})
        item = first_item(raw)
        if item is None:
            raise ValueError(f"Work Item {item_id} was not returned by Polarion")

        data = normalize_work_item(
            item,
            project_id=pid,
            description_limit=description_limit,
            include_custom_fields=include_custom_fields,
        )
        if include_links:
            try:
                forwards = client.get(_item_path(pid, item_id, "linkedworkitems"))
                data["links"] = _normalize_links(forwards, item_id)
            except PolarionError as exc:
                data["links_error"] = exc.to_dict()
            try:
                backwards = client.get(_item_path(pid, item_id, "backlinkedworkitems"))
                data["backlinks"] = _normalize_links(backwards, item_id)
            except PolarionError as exc:
                if exc.status not in {404, 405}:
                    data["backlinks_error"] = exc.to_dict()
        return ok(data, project=pid)

    @mcp.tool()
    @tool
    def polarion_get_work_items(
        work_item_ids: list[str],
        project_id: str | None = None,
        description_limit: int = 1000,
    ) -> dict[str, Any]:
        """Get several Work Items in one call.

        Args:
            work_item_ids: List of Work Item ids, e.g. ['PROJ-1', 'PROJ-2'].
            project_id: Project id. Inferred per item from the id prefix when omitted.
            description_limit: Truncate each description to this many characters.
        """
        ids = [require_id(value, "work_item_ids[]") for value in work_item_ids]
        if not ids:
            raise ValueError("work_item_ids must contain at least one id")

        grouped: dict[str, list[str]] = {}
        for item_id in ids:
            key = _infer_project(runtime, item_id, project_id)
            grouped.setdefault(key, []).append(item_id)

        found: dict[str, dict[str, Any]] = {}
        for pid, group in grouped.items():
            query = "id:(" + " OR ".join(f'"{value}"' for value in group) + ")"
            try:
                raw = client.get_all(
                    f"projects/{pid}/workitems",
                    params={"query": query, "fields[workitems]": "@all"},
                    max_items=len(group) + 5,
                )
                for item in raw:
                    normalized = normalize_work_item(
                        item, project_id=pid, description_limit=description_limit
                    )
                    found[normalized["id"]] = normalized
            except PolarionError:
                pass

            # Fall back per item: some 2023 builds do not support id:(...) grouping.
            for item_id in group:
                if item_id in found:
                    continue
                try:
                    single = client.get(
                        _item_path(pid, item_id), params={"fields[workitems]": "@all"}
                    )
                    item = first_item(single)
                    if item:
                        normalized = normalize_work_item(
                            item, project_id=pid, description_limit=description_limit
                        )
                        found[normalized["id"]] = normalized
                except PolarionError:
                    continue

        ordered = [found[item_id] for item_id in ids if item_id in found]
        missing = [item_id for item_id in ids if item_id not in found]
        return ok(ordered, count=len(ordered), missing=missing)

    @mcp.tool()
    @tool
    def polarion_create_work_item(
        title: str,
        work_item_type: str,
        project_id: str | None = None,
        description: str | None = None,
        status: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        assignees: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a Work Item. Requires write access.

        Args:
            title: Work Item title.
            work_item_type: Type id, e.g. 'requirement', 'task', 'defect'. Use
                polarion_list_work_item_types to discover valid ids.
            project_id: Target project. Defaults to POLARION_DEFAULT_PROJECT.
            description: Description text or HTML.
            status: Initial status id, e.g. 'open'. Must be allowed by the workflow.
            custom_fields: Map of custom field id to value.
            assignees: Optional user ids to assign after creation.
            dry_run: Build and return the JSON:API request without sending it.
        """
        runtime.ensure_write()
        pid = runtime.project(project_id)
        attributes: dict[str, Any] = {
            "title": require_id(title, "title"),
            "type": require_id(work_item_type, "work_item_type"),
        }
        if description is not None:
            attributes["description"] = description
        if status:
            attributes["status"] = status
        if custom_fields:
            attributes.update(custom_fields)

        body = work_item_list_body(attributes, project_id=pid)
        path = f"projects/{pid}/workitems"
        if dry_run:
            return runtime.dry_run("POST", path, body)

        created = first_item(client.post(path, json_body=body))
        if created is None:
            raise ValueError("Polarion accepted the request but returned no Work Item")

        data = normalize_work_item(created, project_id=pid, description_limit=8000)
        if assignees:
            # Some 2023 builds accept assignee only as a relationship.
            try:
                client.patch(
                    _item_path(pid, data["id"]),
                    json_body={
                        "data": {
                            "type": "workitems",
                            "id": data["id"],
                            "relationships": {
                                "assignee": {
                                    "data": [
                                        {"type": "users", "id": user} for user in assignees
                                    ]
                                }
                            },
                        }
                    },
                )
                data["assignees"] = assignees
            except PolarionError as exc:
                data["assignee_error"] = exc.to_dict()
        return ok(data, project=pid, created=True)

    @mcp.tool()
    @tool
    def polarion_update_work_item(
        work_item_id: str,
        project_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        assignees: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Update Work Item fields with PATCH. Only the supplied fields change.

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
            title: New title.
            description: New description text or HTML.
            status: New status. Some workflows only allow status changes through
                polarion_perform_workflow_action; if PATCH is rejected, use that tool.
            custom_fields: Map of custom field id to new value.
            assignees: Replace the assignee list with these user ids.
            dry_run: Build and return the JSON:API request without sending it.
        """
        runtime.ensure_write()
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)

        attributes: dict[str, Any] = {}
        if title is not None:
            attributes["title"] = title
        if description is not None:
            attributes["description"] = description
        if status is not None:
            attributes["status"] = status
        if custom_fields:
            attributes.update(custom_fields)
        if not attributes and assignees is None:
            raise ValueError("Nothing to update: provide at least one field to change")

        document: dict[str, Any] = {"type": "workitems", "id": item_id}
        if attributes:
            document["attributes"] = attributes
        if assignees is not None:
            document["relationships"] = {
                "assignee": {"data": [{"type": "users", "id": user} for user in assignees]}
            }
        body = {"data": document}
        path = _item_path(pid, item_id)
        if dry_run:
            return runtime.dry_run("PATCH", path, body)

        updated = first_item(client.patch(path, json_body=body))
        if updated is None:
            refreshed = client.get(path, params={"fields[workitems]": "@all"})
            updated = first_item(refreshed)
        data = normalize_work_item(updated or {"id": item_id}, project_id=pid, description_limit=8000)
        return ok(data, project=pid, updated=True)

    @mcp.tool()
    @tool
    def polarion_delete_work_item(
        work_item_id: str,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Delete a Work Item permanently. Requires write access.

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
            dry_run: Return the request that would be sent without deleting anything.
        """
        runtime.ensure_write()
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        path = _item_path(pid, item_id)
        if dry_run:
            return runtime.dry_run("DELETE", path, None)
        client.delete(path)
        return ok({"id": item_id, "deleted": True}, project=pid)

    @mcp.tool()
    @tool
    def polarion_get_work_item_revisions(
        work_item_id: str,
        project_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List the revision history of a Work Item (requires Polarion 2310 or newer).

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
            limit: Maximum revisions to return.
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        limit = max(1, min(int(limit), 200))
        try:
            raw = client.get_all(
                _item_path(pid, item_id, "revisions"),
                max_items=limit,
            )
        except PolarionError as exc:
            if exc.status in {404, 405}:
                raise ValueError(
                    "Revision history is not available on this Polarion release or for this "
                    "Work Item. It requires Polarion 2310 or newer."
                ) from exc
            raise

        revisions = []
        for entry in raw:
            attributes = entry.get("attributes") or {}
            revisions.append(
                {
                    "id": entry.get("id"),
                    "author": attributes.get("author"),
                    "created": attributes.get("created"),
                    "message": text_value(attributes.get("message") or attributes.get("comment")),
                }
            )
        return ok(revisions, project=pid, work_item=item_id, count=len(revisions))
