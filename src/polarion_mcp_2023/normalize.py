"""Turn Polarion JSON:API payloads into compact, LLM-friendly dictionaries."""

from __future__ import annotations

from typing import Any, Iterable

# Attributes that Polarion defines on Work Items. Anything else in ``attributes``
# is a configured custom field.
BUILTIN_WORK_ITEM_ATTRS = {
    "id",
    "title",
    "description",
    "status",
    "type",
    "created",
    "updated",
    "resolution",
    "severity",
    "priority",
    "dueDate",
    "initialEstimate",
    "remainingEstimate",
    "timeSpent",
    "author",
    "assignee",
    "categories",
    "hyperlinks",
    "votes",
    "watchers",
    "approvals",
    "attachments",
    "comments",
    "workRecords",
    "linkedWorkItems",
    "backlinkedWorkItems",
    "externalLinks",
    "location",
    "outlineNumber",
    "plannedStart",
    "plannedEnd",
    "startedOn",
    "finishedOn",
    "calculatedTimeSpent",
}

BUILTIN_PROJECT_ATTRS = {
    "id",
    "name",
    "description",
    "active",
    "startDate",
    "finishDate",
    "lead",
    "status",
    "template",
    "location",
    "archived",
    "created",
    "updated",
    "workItemTypes",
    "categories",
}


def text_value(value: Any) -> str | None:
    """Polarion wraps rich text as ``{"type": "text/html", "value": "..."}``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("value", "text", "content"):
            inner = value.get(key)
            if isinstance(inner, str):
                return inner
            if isinstance(inner, (int, float)):
                return str(inner)
        return None
    if isinstance(value, list):
        return ", ".join(str(text_value(v)) for v in value if text_value(v) is not None) or None
    return str(value)


def truncate(value: str | None, limit: int | None) -> tuple[str | None, bool]:
    if value is None or limit is None or limit <= 0:
        return value, False
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def rel_ids(raw: dict[str, Any], name: str) -> list[str]:
    relationships = raw.get("relationships") or {}
    node = relationships.get(name) or {}
    data = node.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        item_id = data.get("id")
        return [str(item_id)] if item_id is not None else []
    if isinstance(data, list):
        return [str(item["id"]) for item in data if isinstance(item, dict) and item.get("id") is not None]
    return []


def rel_id(raw: dict[str, Any], name: str) -> str | None:
    ids = rel_ids(raw, name)
    return ids[0] if ids else None


def self_link(raw: dict[str, Any]) -> str | None:
    links = raw.get("links") or {}
    value = links.get("self")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("href") or value.get("url")
    return None


def normalize_work_item(
    raw: dict[str, Any],
    *,
    project_id: str | None = None,
    description_limit: int | None = 4000,
    include_custom_fields: bool = True,
) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    item_id = str(raw.get("id") or attributes.get("id") or "")

    out: dict[str, Any] = {"id": item_id}

    title = text_value(attributes.get("title"))
    if title is not None:
        out["title"] = title

    out["type"] = (
        text_value(attributes.get("type"))
        or rel_id(raw, "type")
        or (attributes.get("type") if isinstance(attributes.get("type"), str) else None)
    )
    out["status"] = text_value(attributes.get("status")) or rel_id(raw, "status")

    for field in (
        "resolution",
        "severity",
        "priority",
        "dueDate",
        "initialEstimate",
        "remainingEstimate",
        "timeSpent",
        "created",
        "updated",
        "outlineNumber",
        "plannedStart",
        "plannedEnd",
    ):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value

    project = rel_id(raw, "project") or project_id
    if project:
        out["project"] = project

    author = rel_id(raw, "author") or text_value(attributes.get("author"))
    if author:
        out["author"] = author

    assignees = rel_ids(raw, "assignee")
    if assignees:
        out["assignees"] = assignees

    categories = rel_ids(raw, "categories")
    if categories:
        out["categories"] = categories

    description = text_value(attributes.get("description"))
    if description is not None:
        trimmed, truncated = truncate(description, description_limit)
        out["description"] = trimmed
        if truncated:
            out["description_truncated"] = True

    linked = rel_ids(raw, "linkedWorkItems")
    if linked:
        out["linked_work_items"] = linked
    backlinked = rel_ids(raw, "backlinkedWorkItems")
    if backlinked:
        out["backlinked_work_items"] = backlinked

    if include_custom_fields:
        custom: dict[str, Any] = {}
        # Some builds expose custom fields under a container, others flatten them
        # into attributes. Support both, flat keys win.
        container = attributes.get("customFields")
        if isinstance(container, dict):
            for key, value in container.items():
                converted = text_value(value)
                if converted is not None:
                    custom[key] = converted
        for key, value in attributes.items():
            if key in BUILTIN_WORK_ITEM_ATTRS or key == "customFields":
                continue
            converted = text_value(value)
            if converted is not None:
                custom[key] = converted
        if custom:
            out["custom_fields"] = custom

    url = self_link(raw)
    if url:
        out["self_link"] = url
    return out


def normalize_project(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("name", "description", "status", "startDate", "finishDate", "template", "location"):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value
    for field in ("active", "archived"):
        if isinstance(attributes.get(field), bool):
            out[field] = attributes[field]
    lead = rel_id(raw, "lead") or text_value(attributes.get("lead"))
    if lead:
        out["lead"] = lead
    types = rel_ids(raw, "workItemTypes")
    if types:
        out["work_item_types"] = types
    url = self_link(raw)
    if url:
        out["self_link"] = url
    return out


def normalize_document(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("title", "name", "type", "status", "created", "updated", "location", "spaceId"):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value
    content = text_value(attributes.get("homePageContent"))
    if content is not None:
        out["content"] = content
    url = self_link(raw)
    if url:
        out["self_link"] = url
    return out


def normalize_document_part(raw: dict[str, Any], *, content_limit: int | None = 20000) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("title", "type", "level", "outlineNumber", "order"):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value
    content = text_value(attributes.get("content"))
    if content is None:
        content = text_value(attributes.get("text"))
    if content is not None:
        trimmed, truncated = truncate(content, content_limit)
        out["content"] = trimmed
        if truncated:
            out["content_truncated"] = True
    work_item_id = rel_id(raw, "workItem") or text_value(attributes.get("workItem"))
    if work_item_id:
        out["work_item_id"] = work_item_id
    return out


def normalize_test_run(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in (
        "title",
        "status",
        "type",
        "created",
        "updated",
        "startedOn",
        "finishedOn",
        "plannedStart",
        "plannedEnd",
        "isTemplate",
    ):
        value = attributes.get(field)
        if isinstance(value, bool):
            out[field] = value
        else:
            text = text_value(value)
            if text not in (None, ""):
                out[field] = text
    template = rel_id(raw, "template") or text_value(attributes.get("template"))
    if template:
        out["template"] = template
    url = self_link(raw)
    if url:
        out["self_link"] = url
    return out


def normalize_test_record(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("result", "executed", "duration", "comment", "defect"):
        value = text_value(attributes.get(field))
        if value not in (None, ""):
            out[field] = value
    test_case = rel_id(raw, "testCase") or text_value(attributes.get("testCase"))
    if test_case:
        out["test_case"] = test_case
    user = rel_id(raw, "user") or text_value(attributes.get("user"))
    if user:
        out["user"] = user
    return out


def normalize_user(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("name", "email", "description", "disabled", "gender"):
        value = attributes.get(field)
        if isinstance(value, bool):
            out[field] = value
        else:
            text = text_value(value)
            if text not in (None, ""):
                out[field] = text
    return out


def normalize_enumeration(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes") or {}
    out: dict[str, Any] = {"id": str(raw.get("id") or attributes.get("id") or "")}
    for field in ("name", "description", "type", "hidden"):
        value = attributes.get(field)
        if isinstance(value, bool):
            out[field] = value
        else:
            text = text_value(value)
            if text not in (None, ""):
                out[field] = text
    options = attributes.get("options")
    if isinstance(options, list):
        normalized_options = []
        for option in options:
            if isinstance(option, dict):
                normalized_options.append(
                    {
                        "id": option.get("id"),
                        "name": option.get("name"),
                        "default": option.get("default", False),
                    }
                )
            else:
                normalized_options.append({"id": str(option)})
        out["options"] = normalized_options
    return out


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
