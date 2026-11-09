from __future__ import annotations

from conftest import call_tool


def test_list_spaces(runtime, mcp):
    result = call_tool(mcp, "polarion_list_spaces")
    assert result["ok"] is True
    assert [space["id"] for space in result["data"]] == ["Specification", "Design"]


def test_list_documents_falls_back_to_spaces(runtime, mcp):
    """2310 has no project-wide document collection; the tool must walk spaces."""
    result = call_tool(mcp, "polarion_list_documents")
    assert result["ok"] is True
    assert result["meta"]["count"] == 2
    titles = {document["id"] for document in result["data"]}
    assert titles == {"System Requirements", "Interface Spec"}
    assert all(document["space_id"] == "Specification" for document in result["data"])


def test_list_documents_in_one_space(runtime, mcp):
    result = call_tool(mcp, "polarion_list_documents", space_id="Specification")
    assert result["meta"]["count"] == 2


def test_get_document_resolves_space(runtime, mcp):
    result = call_tool(mcp, "polarion_get_document", document_id="System Requirements")
    assert result["ok"] is True
    assert result["meta"]["space"] == "Specification"
    assert "Power supply" in result["data"]["content"]


def test_get_document_with_parts(runtime, mcp):
    result = call_tool(
        mcp,
        "polarion_get_document",
        document_id="System Requirements",
        include_parts=True,
    )
    assert result["ok"] is True
    assert len(result["data"]["parts"]) == 2


def test_get_document_parts_and_work_items(runtime, mcp):
    result = call_tool(
        mcp,
        "polarion_get_document_parts",
        document_id="System Requirements",
        space_id="Specification",
        include_work_item_content=True,
    )
    assert result["ok"] is True
    parts = result["data"]
    assert len(parts) == 2
    second = next(part for part in parts if part["id"] == "part-2")
    assert second["work_item_id"] == "DEMO-1"
    assert second["work_item"]["title"] == "Power supply requirement"


def test_get_document_parts_requires_space(runtime, mcp):
    result = call_tool(mcp, "polarion_get_document_parts", document_id="System Requirements")
    assert result["ok"] is False
    assert result["error"]["kind"] == "invalid_argument"
    assert "space_id" in result["error"]["message"]


def test_missing_document_is_reported(runtime, mcp):
    result = call_tool(mcp, "polarion_get_document", document_id="Nope")
    assert result["ok"] is False
    assert "not found" in result["error"]["message"].lower()
