"""Enumerations, users, comments, workflow actions and attachments."""

from __future__ import annotations

import mimetypes
import os
from typing import Any
from urllib.parse import quote

from ..errors import PolarionError
from ..normalize import normalize_enumeration, normalize_user, text_value
from ..runtime import Runtime, first_item, items, ok, require_id, tool
from .workitems import _infer_project, _item_path

MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client

    @mcp.tool()
    @tool
    def polarion_list_enumerations(
        project_id: str | None = None,
        enumeration_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List project enumerations (status, severity, priority, type, ...), or read one
        enumeration with its allowed values.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            enumeration_id: Optional enumeration id, e.g. 'status' or 'workItemType'.
            limit: Maximum enumerations to return.
        """
        pid = runtime.project(project_id)
        if enumeration_id:
            raw = client.get(f"projects/{pid}/enumerations/{enumeration_id}")
            return ok(normalize_enumeration(first_item(raw) or {}), project=pid)

        raw = client.get_all(
            f"projects/{pid}/enumerations",
            params={"fields[enumerations]": "@basic"},
            max_items=max(1, min(int(limit), 300)),
        )
        enumerations = [normalize_enumeration(item) for item in raw]
        return ok(
            enumerations,
            project=pid,
            count=len(enumerations),
            note="Pass enumeration_id to get the allowed values (options) of one enumeration.",
        )

    @mcp.tool()
    @tool
    def polarion_list_users(
        query: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List Polarion users, optionally filtered by name, id or email.

        Args:
            query: Case-insensitive substring filter.
            limit: Maximum users to return.
            project_id: Optional project id; used only if the global /users endpoint is
                unavailable on this release.
        """
        limit = max(1, min(int(limit), 300))
        raw: list[dict[str, Any]] | None = None
        try:
            raw = client.get_all(
                "users",
                params={"fields[users]": "@basic"},
                max_items=config_limit(runtime, limit, query),
            )
        except PolarionError as exc:
            if exc.status not in {403, 404, 405}:
                raise
            if not (project_id or runtime.config.default_project):
                raise ValueError(
                    "The global /users endpoint is unavailable on this server. "
                    "Pass project_id to read project users instead."
                ) from exc
            pid = runtime.project(project_id)
            raw = client.get_all(
                f"projects/{pid}/users",
                params={"fields[users]": "@basic"},
                max_items=config_limit(runtime, limit, query),
            )

        users = [normalize_user(item) for item in raw]
        if query and query.strip():
            needle = query.strip().lower()
            users = [
                user
                for user in users
                if needle in str(user.get("id", "")).lower()
                or needle in str(user.get("name", "")).lower()
                or needle in str(user.get("email", "")).lower()
            ]
        return ok(users[:limit], count=min(len(users), limit))

    @mcp.tool()
    @tool
    def polarion_add_comment(
        work_item_id: str,
        text: str,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Add a comment to a Work Item. Requires write access.

        Args:
            work_item_id: Work Item id.
            text: Comment text or HTML.
            project_id: Project id. Inferred from the id when omitted.
            dry_run: Build the request without sending it.
        """
        runtime.ensure_write()
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        body = {"data": [{"type": "comments", "attributes": {"text": text}}]}
        path = _item_path(pid, item_id, "comments")
        if dry_run:
            return runtime.dry_run("POST", path, body)
        try:
            created = client.post(path, json_body=body)
        except PolarionError as exc:
            if exc.status not in {400, 422}:
                raise
            # Rich-text aware builds expect a typed value object.
            created = client.post(
                path,
                json_body={
                    "data": [
                        {
                            "type": "comments",
                            "attributes": {"text": {"type": "text/html", "value": text}},
                        }
                    ]
                },
            )
        return ok(items(created), project=pid, work_item=item_id, created=True)

    @mcp.tool()
    @tool
    def polarion_get_comments(
        work_item_id: str,
        project_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List comments on a Work Item.

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
            limit: Maximum comments to return.
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        raw = client.get_all(
            _item_path(pid, item_id, "comments"),
            params={"fields[comments]": "@all"},
            max_items=max(1, min(int(limit), 500)),
        )
        comments = []
        for entry in raw:
            attributes = entry.get("attributes") or {}
            comments.append(
                {
                    "id": entry.get("id"),
                    "text": text_value(attributes.get("text")),
                    "created": attributes.get("created"),
                    "author": attributes.get("author")
                    or ((entry.get("relationships") or {}).get("author", {}).get("data") or {}).get("id"),
                }
            )
        return ok(comments, project=pid, work_item=item_id, count=len(comments))

    @mcp.tool()
    @tool
    def polarion_get_workflow_actions(
        work_item_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List the workflow actions currently allowed for a Work Item (Polarion 2310+).

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        try:
            raw = client.get(_item_path(pid, item_id, "actions"))
        except PolarionError as exc:
            if exc.status in {404, 405}:
                raise ValueError(
                    "Workflow actions are not available on this Polarion release. "
                    "They require Polarion 2310 or newer."
                ) from exc
            raise

        actions = []
        for entry in items(raw):
            attributes = entry.get("attributes") or {}
            actions.append(
                {
                    "id": entry.get("id") or attributes.get("actionId") or attributes.get("id"),
                    "name": attributes.get("name") or attributes.get("label"),
                    "target_status": attributes.get("targetStatus") or attributes.get("status"),
                }
            )
        return ok(actions, project=pid, work_item=item_id, count=len(actions))

    @mcp.tool()
    @tool
    def polarion_perform_workflow_action(
        work_item_id: str,
        action: str,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute a workflow action (approve, reopen, ...) on a Work Item.

        Args:
            work_item_id: Work Item id.
            action: Action id returned by polarion_get_workflow_actions.
            project_id: Project id. Inferred from the id when omitted.
            dry_run: Build the request without sending it.
        """
        runtime.ensure_write()
        item_id = require_id(work_item_id, "work_item_id")
        action_id = require_id(action, "action")
        pid = _infer_project(runtime, item_id, project_id)

        path = _item_path(pid, item_id, "actions")
        list_body = {
            "data": [
                {
                    "type": "workflowactions",
                    "attributes": {"actionId": action_id, "action": action_id},
                }
            ]
        }
        if dry_run:
            return runtime.dry_run("POST", path, list_body)

        try:
            response = client.post(path, json_body=list_body)
        except PolarionError as exc:
            if exc.status not in {400, 422}:
                raise
            single_body = {
                "data": {
                    "type": "workflowactions",
                    "attributes": {"actionId": action_id, "action": action_id},
                }
            }
            response = client.post(path, json_body=single_body)
        return ok(
            {"work_item": item_id, "action": action_id, "response": items(response)},
            project=pid,
            executed=True,
        )

    @mcp.tool()
    @tool
    def polarion_list_attachments(
        work_item_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """List attachments of a Work Item.

        Args:
            work_item_id: Work Item id.
            project_id: Project id. Inferred from the id when omitted.
        """
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        raw = client.get_all(
            _item_path(pid, item_id, "attachments"),
            params={"fields[attachments]": "@all"},
            max_items=500,
        )
        attachments = []
        for entry in raw:
            attributes = entry.get("attributes") or {}
            attachments.append(
                {
                    "id": entry.get("id"),
                    "file_name": attributes.get("fileName") or attributes.get("name"),
                    "size": attributes.get("size"),
                    "content_type": attributes.get("contentType"),
                    "created": attributes.get("created"),
                    "author": attributes.get("author"),
                }
            )
        return ok(attachments, project=pid, work_item=item_id, count=len(attachments))

    @mcp.tool()
    @tool
    def polarion_download_attachment(
        work_item_id: str,
        file_name: str,
        project_id: str | None = None,
        save_dir: str | None = None,
    ) -> dict[str, Any]:
        """Download a Work Item attachment to the local filesystem and return its path.

        Args:
            work_item_id: Work Item id.
            file_name: Attachment file name as listed by polarion_list_attachments.
            project_id: Project id. Inferred from the id when omitted.
            save_dir: Directory to write into. Defaults to ./polarion_attachments.
        """
        item_id = require_id(work_item_id, "work_item_id")
        safe_name = os.path.basename(require_id(file_name, "file_name"))
        if safe_name in {"", ".", ".."}:
            raise ValueError("file_name must be a plain file name")
        pid = _infer_project(runtime, item_id, project_id)

        raw = client.request(
            "GET",
            _item_path(pid, item_id, f"attachments/{quote(safe_name, safe='')}"),
            raw=True,
        )
        target_dir = os.path.abspath(save_dir or os.path.join(os.getcwd(), "polarion_attachments"))
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, safe_name)
        with open(target, "wb") as handle:
            handle.write(raw.content)
        return ok(
            {
                "file_name": safe_name,
                "path": target,
                "size": len(raw.content),
                "content_type": raw.content_type,
            },
            project=pid,
            work_item=item_id,
        )

    @mcp.tool()
    @tool
    def polarion_upload_attachment(
        work_item_id: str,
        file_path: str,
        project_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Upload a local file as an attachment to a Work Item. Requires write access.

        Args:
            work_item_id: Work Item id.
            file_path: Absolute or relative path to an existing file (max 50 MB).
            project_id: Project id. Inferred from the id when omitted.
            dry_run: Validate the file and return the request without uploading.
        """
        runtime.ensure_write()
        item_id = require_id(work_item_id, "work_item_id")
        pid = _infer_project(runtime, item_id, project_id)
        path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(path):
            raise ValueError(f"File not found: {path}")
        size = os.path.getsize(path)
        if size > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"File is {size} bytes, above the {MAX_ATTACHMENT_BYTES} byte attachment limit."
            )

        endpoint = _item_path(pid, item_id, "attachments")
        if dry_run:
            return runtime.dry_run(
                "POST",
                endpoint,
                {"multipart": {"file": os.path.basename(path)}, "size": size},
            )

        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            files = {"file": (os.path.basename(path), handle, content_type)}
            response = client.request("POST", endpoint, files=files)

        return ok(
            {"file_name": os.path.basename(path), "size": size, "response": items(response)},
            project=pid,
            work_item=item_id,
            uploaded=True,
        )


def config_limit(runtime: Runtime, limit: int, query: str | None) -> int:
    """Scan more rows when a local substring filter is applied."""
    return runtime.config.max_items if query else limit + 1
