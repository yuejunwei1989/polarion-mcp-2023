"""Tool and prompt surface: names, schemas and count stability."""

from __future__ import annotations

import asyncio

EXPECTED_TOOLS = {
    "polarion_server_info",
    "polarion_list_projects",
    "polarion_get_project",
    "polarion_list_work_item_types",
    "polarion_search_work_items",
    "polarion_get_work_item",
    "polarion_get_work_items",
    "polarion_create_work_item",
    "polarion_update_work_item",
    "polarion_delete_work_item",
    "polarion_get_work_item_revisions",
    "polarion_get_linked_work_items",
    "polarion_link_work_items",
    "polarion_unlink_work_items",
    "polarion_list_spaces",
    "polarion_list_documents",
    "polarion_get_document",
    "polarion_get_document_parts",
    "polarion_list_test_runs",
    "polarion_get_test_run",
    "polarion_create_test_run",
    "polarion_add_test_record",
    "polarion_get_test_records",
    "polarion_list_enumerations",
    "polarion_list_users",
    "polarion_add_comment",
    "polarion_get_comments",
    "polarion_get_workflow_actions",
    "polarion_perform_workflow_action",
    "polarion_list_attachments",
    "polarion_download_attachment",
    "polarion_upload_attachment",
}


def test_tool_names_are_stable(mcp):
    tools = asyncio.run(mcp.list_tools())
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 32


def test_every_tool_has_a_description_and_object_schema(mcp):
    for tool in asyncio.run(mcp.list_tools()):
        assert tool.description, f"{tool.name} has no description"
        assert tool.inputSchema["type"] == "object"
        assert tool.inputSchema.get("properties") is not None


def test_write_tools_expose_dry_run(mcp):
    write_tools = [
        "polarion_create_work_item",
        "polarion_update_work_item",
        "polarion_delete_work_item",
        "polarion_link_work_items",
        "polarion_unlink_work_items",
        "polarion_create_test_run",
        "polarion_add_test_record",
        "polarion_add_comment",
        "polarion_perform_workflow_action",
        "polarion_upload_attachment",
    ]
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    for name in write_tools:
        assert "dry_run" in tools[name].inputSchema["properties"], name


def test_prompts_are_registered(mcp):
    prompts = asyncio.run(mcp.list_prompts())
    assert {prompt.name for prompt in prompts} == {
        "review_requirement",
        "traceability_gaps",
        "prepare_test_run",
    }


def test_prompt_renders_arguments(mcp):
    result = asyncio.run(mcp.get_prompt("review_requirement", {"work_item_id": "DEMO-1"}))
    text = result.messages[0].content.text
    assert "DEMO-1" in text
    assert "polarion_get_work_item" in text
