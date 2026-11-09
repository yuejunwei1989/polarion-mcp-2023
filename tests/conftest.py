"""Shared pytest fixtures: a fake Polarion 2310 server and a ready Runtime."""

from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from fake_polarion import create_server  # noqa: E402

from polarion_mcp_2023.client import PolarionClient  # noqa: E402
from polarion_mcp_2023.config import Config  # noqa: E402
from polarion_mcp_2023.runtime import Runtime  # noqa: E402


@pytest.fixture(scope="session")
def fake_server():
    server, store = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, store
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _reset_store_each_test(fake_server):
    """Keep tests independent: the fake server is session-scoped."""
    fake_server[1].reset()
    yield


@pytest.fixture()
def store(fake_server):
    _, store = fake_server
    store.reset()
    return store


def make_config(fake_server, **overrides) -> Config:
    server, _ = fake_server
    port = server.server_address[1]
    values = dict(
        base_url=f"http://127.0.0.1:{port}/polarion",
        rest_url=f"http://127.0.0.1:{port}/polarion/rest/v1",
        username="alice",
        password="secret",
        token=None,
        auth_mode="basic",
        default_project="DEMO",
        verify_ssl=True,
        timeout=5.0,
        max_retries=0,
        default_page_size=100,
        max_page_size=100,
        max_items=1000,
        api_flavor="auto",
        read_only=False,
        log_level="CRITICAL",
    )
    values.update(overrides)
    return Config(**values)


@pytest.fixture()
def config(fake_server):
    return make_config(fake_server)


@pytest.fixture()
def config_factory(fake_server):
    def _make(**overrides) -> Config:
        return make_config(fake_server, **overrides)

    return _make


@pytest.fixture()
def runtime(config):
    client = PolarionClient(config)
    capabilities = client.probe()
    return Runtime(config, client, capabilities)


@pytest.fixture()
def mcp(runtime):
    from polarion_mcp_2023.server import build_server

    server, _ = build_server(runtime.config, log_level="CRITICAL")
    return server


def call_tool(mcp, name: str, **kwargs):
    """Invoke a registered FastMCP tool function directly."""
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"tool {name} not registered"
    return tool.fn(**kwargs)
