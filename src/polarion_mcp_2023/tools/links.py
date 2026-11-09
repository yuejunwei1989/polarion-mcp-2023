"""Traceability link tools (linked / backlinked Work Items)."""

from __future__ import annotations

from typing import Any

from ..errors import PolarionError
from ..runtime import Runtime, items, ok, require_id, tool
from .workitems import _infer_project, _item_path, _normalize_links


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client

    @mcp.tool()
    @tool
    def polarion_get_linked_work_items(
        work_item_id: str,
        project_id: str | None = None,
        direction: str = "both",
    ) -> dict[str, Any]:
        """Read traceability links of a Work Item, including roles such as 'verifies' or
        'implements'.

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
            direction: 'outgoing', 'incoming' or 'both' (default).
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        if direction not in {"outgoing", "incoming", "both"}:
            raise ValueError("direction must be one of: outgoing, incoming, both")

        result: dict[str, Any] = {"work_item": item_id}
        if direction in {"outgoing", "both"}:
            payload = client.get(_item_path(pid, item_id, "linkedworkitems"))
            result["outgoing"] = _normalize_links(payload, item_id)
        if direction in {"incoming", "both"}:
            try:
                payload = client.get(_item_path(pid, item_id, "backlinkedworkitems"))
                result["incoming"] = _normalize_links(payload, item_id)
            except PolarionError as exc:
                if exc.status not in {404, 405}:
                    raise
                result["incoming"] = []
                result["incoming_note"] = "Backlinked Work Items are not exposed by this release."
        return ok(result, project=pid)

    @mcp.tool()
    @tool
    def polarion_link_work_items(
        source_work_item_id: str,
        target_work_item_id: str,
        role: str,
        project_id: str | None = None,
        suspect: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a directed traceability link from one Work Item to another.

        Args:
            source_work_item_id: The Work Item that owns the outgoing link.
            target_work_item_id: The Work Item being linked to.
            role: Link role id, e.g. 'verifies', 'implements', 'relates_to'. Role ids are
                project-configurable; an invalid role returns HTTP 422 with the allowed values.
            project_id: Project id. Inferred from the source id when omitted.
            suspect: Mark the link as suspect.
            dry_run: Build the request without sending it.
        """
        runtime.ensure_write()
        source = require_id(source_work_item_id, "source_work_item_id")
        target = require_id(target_work_item_id, "target_work_item_id")
        pid = _infer_project(runtime, source, project_id)

        document = {
            "type": "linkedworkitems",
            "attributes": {"role": require_id(role, "role"), "suspect": bool(suspect)},
            "relationships": {
                "target": {"data": {"type": "workitems", "id": target}},
            },
        }
        path = _item_path(pid, source, "linkedworkitems")
        list_body = {"data": [document]}
        if dry_run:
            return runtime.dry_run("POST", path, list_body)

        try:
            created = client.post(path, json_body=list_body)
        except PolarionError as exc:
            if exc.status not in {400, 415, 422}:
                raise
            # A few 2023 builds accept a single document instead of a list.
            created = client.post(path, json_body={"data": document})

        return ok(
            {
                "source": source,
                "target": target,
                "role": role,
                "suspect": bool(suspect),
                "response": items(created),
            },
            project=pid,
            created=True,
        )

    @mcp.tool()
    @tool
    def polarion_unlink_work_items(
        work_item_id: str,
        target_work_item_id: str,
        project_id: str | None = None,
        role: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Remove a traceability link between two Work Items.

        Polarion's delete-link path differs between releases, so this tool discovers the
        existing link first and then tries the documented path variants in order.

        Args:
            work_item_id: The source Work Item id.
            target_work_item_id: The linked Work Item id to remove.
            project_id: Project id. Inferred from the source id when omitted.
            role: Optional role id to disambiguate when several links exist between the pair.
            dry_run: Return the candidate requests without deleting anything.
        """
        runtime.ensure_write()
        source = require_id(work_item_id, "work_item_id")
        target = require_id(target_work_item_id, "target_work_item_id")
        pid = _infer_project(runtime, source, project_id)

        payload = client.get(_item_path(pid, source, "linkedworkitems"))
        existing = [
            link
            for link in _normalize_links(payload, source)
            if link.get("other_work_item") == target
            and (role is None or link.get("role") == role)
        ]
        if not existing:
            raise ValueError(
                f"No link from {source} to {target}"
                + (f" with role '{role}'" if role else "")
                + " was found. Use polarion_get_linked_work_items to inspect existing links."
            )

        link_role = role or existing[0].get("role")
        base = _item_path(pid, source, f"linkedworkitems/{target}")
        candidates = [base]
        if link_role:
            candidates.append(f"{base}?role={link_role}")

        if dry_run:
            return ok(
                {
                    "dry_run": True,
                    "candidates": [{"method": "DELETE", "path": path} for path in candidates],
                    "matched_links": existing,
                }
            )

        errors = []
        for path in candidates:
            try:
                client.delete(path)
                return ok(
                    {"source": source, "target": target, "role": link_role, "deleted": True},
                    project=pid,
                )
            except PolarionError as exc:
                errors.append({"path": path, **exc.to_dict()})
                if exc.status in {401, 403}:
                    break

        return {
            "ok": False,
            "error": {
                "kind": "unlink_failed",
                "message": (
                    f"Could not remove the link from {source} to {target}. "
                    "The delete-link endpoint returned an error for every known path variant."
                ),
                "attempts": errors,
                "hint": "Delete the link in the Polarion UI, or check the REST user guide for your exact build.",
            },
        }
