"""LiveDoc tools: spaces, documents and document parts."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..errors import PolarionError
from ..normalize import normalize_document, normalize_document_part, text_value
from ..runtime import Runtime, first_item, items, ok, require_id, tool


def _seg(value: str) -> str:
    """Encode a path segment. Space and document names may contain spaces and slashes."""
    return quote(value, safe="")


def _space(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("name", "description", "type", "location"):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value
    return out


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client

    @mcp.tool()
    @tool
    def polarion_list_spaces(
        project_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List the LiveDoc spaces (folders) of a project.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            limit: Maximum spaces to return.
        """
        pid = runtime.project(project_id)
        raw = client.get_all(
            f"projects/{pid}/spaces",
            params={"fields[spaces]": "@basic"},
            max_items=max(1, min(int(limit), 500)),
        )
        spaces = [_space(item) for item in raw]
        return ok(spaces, project=pid, count=len(spaces))

    @mcp.tool()
    @tool
    def polarion_list_documents(
        project_id: str | None = None,
        space_id: str | None = None,
        limit: int = 100,
        include_content: bool = False,
    ) -> dict[str, Any]:
        """List LiveDoc documents in a project or in one space.

        On Polarion 2023 there is no project-wide document search endpoint, so when
        space_id is omitted this walks all spaces and merges the results.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            space_id: Optional space id to restrict the listing.
            limit: Maximum documents to return.
            include_content: Include the document home page HTML (can be large).
        """
        pid = runtime.project(project_id)
        limit = max(1, min(int(limit), 500))
        fields = "@all" if include_content else "@basic"

        documents: list[dict[str, Any]] = []
        if space_id:
            raw = client.get_all(
                f"projects/{pid}/spaces/{_seg(space_id)}/documents",
                params={"fields[documents]": fields},
                max_items=limit,
            )
            documents = [normalize_document(item) for item in raw]
            for document in documents:
                document.setdefault("space_id", space_id)
        else:
            project_wide = None
            try:
                project_wide = client.get_all(
                    f"projects/{pid}/documents",
                    params={"fields[documents]": fields},
                    max_items=limit,
                )
            except PolarionError as exc:
                if exc.status not in {404, 405}:
                    raise
            if project_wide is not None:
                documents = [normalize_document(item) for item in project_wide]
            else:
                spaces = client.get_all(
                    f"projects/{pid}/spaces",
                    params={"fields[spaces]": "@basic"},
                    max_items=200,
                )
                for space in spaces:
                    space_id_value = space.get("id")
                    if not space_id_value:
                        continue
                    try:
                        raw = client.get_all(
                            f"projects/{pid}/spaces/{_seg(str(space_id_value))}/documents",
                            params={"fields[documents]": fields},
                            max_items=max(1, limit - len(documents)),
                        )
                    except PolarionError as exc:
                        if exc.status in {403, 404, 405}:
                            continue
                        raise
                    for item in raw:
                        document = normalize_document(item)
                        document.setdefault("space_id", str(space_id_value))
                        documents.append(document)
                    if len(documents) >= limit:
                        break

        return ok(documents[:limit], project=pid, count=min(len(documents), limit))

    @mcp.tool()
    @tool
    def polarion_get_document(
        document_id: str,
        project_id: str | None = None,
        space_id: str | None = None,
        include_parts: bool = False,
        content_limit: int = 20000,
    ) -> dict[str, Any]:
        """Read one LiveDoc document. When space_id is omitted, the document is located by
        walking the project's spaces.

        Args:
            document_id: Document name or id, e.g. 'System Requirements'.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            space_id: Space id. Provide it to avoid the lookup walk.
            include_parts: Also return the document's parts (sections and embedded Work Items).
            content_limit: Truncate HTML content to this many characters.
        """
        pid = runtime.project(project_id)
        doc_id = require_id(document_id, "document_id")

        resolved_space = space_id
        raw: Any = None
        if resolved_space:
            raw = client.get(
                f"projects/{pid}/spaces/{_seg(resolved_space)}/documents/{_seg(doc_id)}",
                params={"fields[documents]": "@all"},
            )
        else:
            spaces = client.get_all(
                f"projects/{pid}/spaces",
                params={"fields[spaces]": "@basic"},
                max_items=200,
            )
            for space in spaces:
                candidate_space = str(space.get("id") or "")
                if not candidate_space:
                    continue
                try:
                    raw = client.get(
                        f"projects/{pid}/spaces/{_seg(candidate_space)}/documents/{_seg(doc_id)}",
                        params={"fields[documents]": "@all"},
                    )
                    resolved_space = candidate_space
                    break
                except PolarionError as exc:
                    if exc.status in {403, 404}:
                        continue
                    raise
            if raw is None:
                raise ValueError(
                    f"Document '{doc_id}' was not found in any space of project {pid}. "
                    "Use polarion_list_spaces and polarion_list_documents to find its id."
                )

        document = normalize_document(first_item(raw) or {})
        if resolved_space:
            document.setdefault("space_id", resolved_space)
        content = document.get("content")
        if isinstance(content, str) and len(content) > content_limit > 0:
            document["content"] = content[:content_limit]
            document["content_truncated"] = True

        if include_parts and resolved_space:
            try:
                parts_payload = client.get(
                    f"projects/{pid}/spaces/{_seg(resolved_space)}/documents/{_seg(doc_id)}/parts",
                    params={"fields[documentparts]": "@all"},
                )
                document["parts"] = [
                    normalize_document_part(part, content_limit=1500)
                    for part in items(parts_payload)
                ]
            except PolarionError as exc:
                document["parts_error"] = exc.to_dict()

        return ok(document, project=pid, space=resolved_space)

    @mcp.tool()
    @tool
    def polarion_get_document_parts(
        document_id: str,
        project_id: str | None = None,
        space_id: str | None = None,
        part_id: str | None = None,
        content_limit: int = 8000,
        include_work_item_content: bool = False,
    ) -> dict[str, Any]:
        """Read the parts of a LiveDoc: headings and sections with their text and linked
        Work Item ids.

        Args:
            document_id: Document name or id.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            space_id: Space id. Required on releases without a project-wide document lookup.
            part_id: Return only this part instead of the full list.
            content_limit: Truncate each part's HTML content.
            include_work_item_content: For Work Item parts, also fetch the Work Item title
                and description (adds one REST call per distinct Work Item, capped at 50).
        """
        pid = runtime.project(project_id)
        doc_id = require_id(document_id, "document_id")
        if not space_id:
            raise ValueError(
                "space_id is required for document parts on Polarion 2023. "
                "Call polarion_list_documents to find the document's space id."
            )

        base = f"projects/{pid}/spaces/{_seg(space_id)}/documents/{_seg(doc_id)}/parts"
        path = f"{base}/{_seg(part_id)}" if part_id else base
        raw = client.get(path, params={"fields[documentparts]": "@all"})
        entries = [first_item(raw)] if part_id else items(raw)
        parts = [
            normalize_document_part(entry, content_limit=content_limit)
            for entry in entries
            if isinstance(entry, dict)
        ]

        if include_work_item_content:
            work_item_ids = []
            for part in parts:
                value = part.get("work_item_id")
                if value and value not in work_item_ids:
                    work_item_ids.append(str(value))
            work_item_ids = work_item_ids[:50]
            if work_item_ids:
                query = "id:(" + " OR ".join(f'"{value}"' for value in work_item_ids) + ")"
                try:
                    found = client.get_all(
                        f"projects/{pid}/workitems",
                        params={"query": query, "fields[workitems]": "@all"},
                        max_items=len(work_item_ids) + 5,
                    )
                    from ..normalize import normalize_work_item

                    mapping = {
                        str(item.get("id")): normalize_work_item(
                            item, project_id=pid, description_limit=1500
                        )
                        for item in found
                    }
                    for part in parts:
                        value = part.get("work_item_id")
                        if value and str(value) in mapping:
                            part["work_item"] = mapping[str(value)]
                except PolarionError as exc:
                    return ok(parts, project=pid, space=space_id, work_item_error=exc.to_dict())

        return ok(
            parts,
            project=pid,
            space=space_id,
            document=doc_id,
            count=len(parts),
            note=(
                "Parts are returned in document order. 'level' and 'outline_number' describe "
                "the section hierarchy."
            ),
        )
