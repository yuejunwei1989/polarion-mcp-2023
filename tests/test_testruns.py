from __future__ import annotations

from conftest import call_tool


def test_create_test_run(runtime, mcp):
    result = call_tool(mcp, "polarion_create_test_run", title="Release 1 regression")
    assert result["ok"] is True
    assert result["data"]["title"] == "Release 1 regression"
    assert result["data"]["id"] == "TR-1"


def test_add_test_record_and_summary(runtime, mcp):
    run = call_tool(mcp, "polarion_create_test_run", title="Sprint 12")
    run_id = run["data"]["id"]

    record = call_tool(
        mcp,
        "polarion_add_test_record",
        test_run_id=run_id,
        test_case_id="DEMO-1",
        result="passed",
        comment="Verified on bench",
    )
    assert record["ok"] is True
    assert record["data"]["result"] == "passed"
    assert record["data"]["test_case"] == "DEMO-1"

    call_tool(
        mcp,
        "polarion_add_test_record",
        test_run_id=run_id,
        test_case_id="DEMO-3",
        result="failed",
    )

    fetched = call_tool(mcp, "polarion_get_test_run", test_run_id=run_id)
    assert fetched["data"]["summary"] == {"passed": 1, "failed": 1}
    assert len(fetched["data"]["records"]) == 2


def test_list_test_runs(runtime, mcp):
    call_tool(mcp, "polarion_create_test_run", title="Run A")
    call_tool(mcp, "polarion_create_test_run", title="Run B")
    result = call_tool(mcp, "polarion_list_test_runs")
    assert result["ok"] is True
    assert result["meta"]["count"] == 2


def test_test_run_dry_run(runtime, mcp):
    result = call_tool(
        mcp, "polarion_create_test_run", title="Preview", dry_run=True
    )
    assert result["data"]["dry_run"] is True
    listing = call_tool(mcp, "polarion_list_test_runs")
    assert listing["meta"]["count"] == 0
