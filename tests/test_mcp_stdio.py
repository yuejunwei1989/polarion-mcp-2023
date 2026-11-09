"""End-to-end MCP protocol test over stdio against the fake 2310 server."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_stdio_initialize_list_and_call(fake_server):
    port = fake_server[0].server_address[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "POLARION_URL": f"http://127.0.0.1:{port}/polarion",
        "POLARION_USERNAME": "alice",
        "POLARION_PASSWORD": "secret",
        "POLARION_DEFAULT_PROJECT": "DEMO",
        "POLARION_LOG_LEVEL": "ERROR",
        "POLARION_MCP_TRANSPORT": "stdio",
    }

    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "polarion_mcp_2023"],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                info = await session.call_tool("polarion_server_info", {})
                search = await session.call_tool(
                    "polarion_search_work_items", {"query": "type:requirement"}
                )
                return init, tools, info, search

    init, tools, info, search = asyncio.run(run())

    assert init.serverInfo.name == "polarion-2023"
    assert len(tools.tools) == 32

    names = {tool.name for tool in tools.tools}
    assert "polarion_search_work_items" in names
    assert "polarion_create_work_item" in names
    assert "polarion_server_info" in names

    create_schema = next(tool for tool in tools.tools if tool.name == "polarion_create_work_item")
    assert "title" in create_schema.inputSchema["required"]
    assert "work_item_type" in create_schema.inputSchema["required"]

    info_payload = json.loads(info.content[0].text)
    assert info_payload["ok"] is True
    assert info_payload["data"]["flavor"] == "2023"
    assert info_payload["data"]["capabilities"]["metadata"] is False

    if search.structuredContent:
        search_payload = search.structuredContent
    else:
        search_payload = json.loads(search.content[0].text)
    assert search_payload["ok"] is True
    assert search_payload["meta"]["count"] == 1
    assert search_payload["data"][0]["id"] == "DEMO-1"
