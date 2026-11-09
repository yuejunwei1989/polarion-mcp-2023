from __future__ import annotations

import pytest
from conftest import call_tool, make_config


def test_not_found_has_actionable_hint(runtime, mcp):
    result = call_tool(mcp, "polarion_get_work_item", work_item_id="DEMO-999")
    assert result["ok"] is False
    error = result["error"]
    assert error["status"] == 404
    assert "2404" in error["hint"]


def test_validation_error_from_polarion(runtime, mcp):
    result = call_tool(
        mcp,
        "polarion_update_work_item",
        work_item_id="DEMO-1",
        custom_fields={"unknownField": "x"},
    )
    assert result["ok"] is False
    assert result["error"]["status"] == 422
    assert "validation" in result["error"]["hint"].lower()


def test_read_only_blocks_writes(fake_server):
    from polarion_mcp_2023.server import build_server

    config = make_config(fake_server, read_only=True)
    read_only_mcp, _ = build_server(config, log_level="CRITICAL")

    result = call_tool(
        read_only_mcp, "polarion_create_work_item", title="x", work_item_type="task"
    )
    assert result["ok"] is False
    assert result["error"]["kind"] == "read_only"
    assert "POLARION_READ_ONLY" in result["error"]["hint"]


def test_invalid_argument_is_reported(runtime, mcp):
    result = call_tool(mcp, "polarion_get_work_item", work_item_id="")
    assert result["ok"] is False
    assert result["error"]["kind"] == "invalid_argument"


def test_unsupported_capability_is_explained(runtime):
    # Directly exercise Runtime.require for a 2404-only capability.
    runtime.capabilities = type(runtime.capabilities).for_release(2310, flavor="2023")
    with pytest.raises(ValueError, match="fields_metadata"):
        runtime.require("fields_metadata", "2404")


def test_server_info_reports_2023(runtime, mcp):
    result = call_tool(mcp, "polarion_server_info")
    data = result["data"]
    assert data["flavor"] == "2023"
    assert data["release"] == 2310
    assert data["capabilities"]["metadata"] is False
    assert any("metadata" in entry for entry in data["unsupported_on_this_release"])
    assert data["auth_mode"] == "basic"


def test_startup_error_is_surfaced_without_crashing(fake_server):
    """A wrong password must not prevent the MCP server from starting; the error is
    reported by polarion_server_info instead of a process exit."""
    from polarion_mcp_2023.server import build_server

    config = make_config(fake_server, password="wrong")
    server, runtime = build_server(config, log_level="CRITICAL")
    assert runtime.startup_error is not None

    result = call_tool(server, "polarion_server_info")
    assert result["ok"] is True
    assert result["data"]["ready"] is False
    assert result["data"]["startup_error"]["status"] == 401
