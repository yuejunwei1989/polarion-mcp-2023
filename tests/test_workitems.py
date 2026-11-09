from __future__ import annotations

from conftest import call_tool


def test_search_returns_all(runtime, mcp):
    result = call_tool(mcp, "polarion_search_work_items")
    assert result["ok"] is True
    assert result["meta"]["count"] == 3
    assert result["meta"]["has_more"] is False
    assert {item["id"] for item in result["data"]} == {"DEMO-1", "DEMO-2", "DEMO-3"}


def test_search_with_lucene_query(runtime, mcp):
    result = call_tool(mcp, "polarion_search_work_items", query="type:requirement")
    assert result["meta"]["count"] == 1
    assert result["data"][0]["id"] == "DEMO-1"


def test_search_pagination_and_has_more(runtime, mcp):
    result = call_tool(mcp, "polarion_search_work_items", limit=2, page_size=2)
    assert len(result["data"]) == 2
    assert result["meta"]["has_more"] is True


def test_get_work_item_with_custom_fields(runtime, mcp):
    result = call_tool(mcp, "polarion_get_work_item", work_item_id="DEMO-1")
    assert result["ok"] is True
    item = result["data"]
    assert item["title"] == "Power supply requirement"
    assert item["type"] == "requirement"
    assert item["status"] == "open"
    assert item["custom_fields"]["safetyLevel"] == "ASIL-B"
    assert "24 V" in item["description"]


def test_get_work_item_links(runtime, mcp):
    result = call_tool(mcp, "polarion_get_work_item", work_item_id="DEMO-2")
    links = result["data"]["links"]
    assert len(links) == 1
    assert links[0]["role"] == "implements"
    assert links[0]["other_work_item"] == "DEMO-1"
    assert links[0]["direction"] == "outgoing"


def test_get_work_items_batch(runtime, mcp):
    result = call_tool(
        mcp, "polarion_get_work_items", work_item_ids=["DEMO-1", "DEMO-3", "DEMO-999"]
    )
    assert result["ok"] is True
    assert [item["id"] for item in result["data"]] == ["DEMO-1", "DEMO-3"]
    assert result["meta"]["missing"] == ["DEMO-999"]


def test_create_update_delete(runtime, mcp):
    created = call_tool(
        mcp,
        "polarion_create_work_item",
        title="New requirement",
        work_item_type="requirement",
        description="<p>Fresh</p>",
    )
    assert created["ok"] is True
    new_id = created["data"]["id"]
    assert created["data"]["title"] == "New requirement"

    updated = call_tool(
        mcp, "polarion_update_work_item", work_item_id=new_id, title="Renamed"
    )
    assert updated["ok"] is True
    assert updated["data"]["title"] == "Renamed"

    deleted = call_tool(mcp, "polarion_delete_work_item", work_item_id=new_id)
    assert deleted["ok"] is True
    assert deleted["data"]["deleted"] is True

    after = call_tool(mcp, "polarion_get_work_item", work_item_id=new_id)
    assert after["ok"] is False
    assert after["error"]["status"] == 404


def test_dry_run_does_not_write(runtime, mcp):
    before = call_tool(mcp, "polarion_search_work_items")["meta"]["count"]
    result = call_tool(
        mcp,
        "polarion_create_work_item",
        title="Preview only",
        work_item_type="task",
        dry_run=True,
    )
    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert result["data"]["request"]["method"] == "POST"
    after = call_tool(mcp, "polarion_search_work_items")["meta"]["count"]
    assert before == after


def test_revisions(runtime, mcp):
    result = call_tool(mcp, "polarion_get_work_item_revisions", work_item_id="DEMO-1")
    assert result["ok"] is True
    assert result["meta"]["count"] == 1


def test_work_item_types_from_project_relationship(runtime, mcp):
    result = call_tool(mcp, "polarion_list_work_item_types")
    assert result["ok"] is True
    assert set(result["data"]) == {"requirement", "task", "defect"}


def test_get_project(runtime, mcp):
    result = call_tool(mcp, "polarion_get_project")
    assert result["ok"] is True
    assert result["data"]["id"] == "DEMO"
    assert result["data"]["name"] == "Demo Project"
    assert result["data"]["capabilities"]["documents"] is True


def test_list_projects_filter(runtime, mcp):
    result = call_tool(mcp, "polarion_list_projects", query="other")
    assert result["meta"]["count"] == 1
    assert result["data"][0]["id"] == "OTHER"
