"""MCP server bootstrap."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .capabilities import Capabilities
from .client import PolarionClient
from .config import Config
from .runtime import Runtime
from .tools import documents, links, misc, projects, testruns, workitems

LOG = logging.getLogger("polarion_mcp")

INSTRUCTIONS = """\
This server connects an AI assistant to a Siemens Polarion ALM instance, with explicit support
for Polarion 2023 (2304 / 2310) servers as well as newer releases.

Key behaviour:
- Start with polarion_server_info to see the detected release and which REST capabilities exist.
- Polarion 2023 has NO /metadata endpoint and no custom-field-configuration, fields-metadata,
  license, jobs or collections endpoints. Tools do not call them in 2023 mode.
- Work Item ids look like PROJECT-123. If POLARION_DEFAULT_PROJECT is unset, pass project_id.
- Searches use Polarion's Lucene syntax, e.g. 'type:requirement AND status:open'.
- Writes are real. Prefer dry_run=true first for create/update/delete calls.
- The server enforces the permissions of the configured Polarion user. A 403 means that user
  cannot perform the operation, not that the tool is broken.
"""


def build_server(
    config: Config | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    log_level: str | None = None,
) -> tuple[FastMCP, Runtime]:
    config = config or Config.from_env()
    level = (log_level or config.log_level or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = PolarionClient(config)
    startup_error: Any = None
    try:
        capabilities = client.probe()
    except Exception as exc:  # keep the server reachable so errors are explainable
        LOG.error("Startup probe failed: %s", exc)
        capabilities = Capabilities.for_release(
            None,
            flavor="2023" if config.api_flavor != "2025" else "2025",
            detected_by="fallback",
            max_page_size=config.max_page_size,
            default_page_size=config.default_page_size,
        )
        startup_error = exc

    runtime = Runtime(config, client, capabilities)
    runtime.startup_error = startup_error  # type: ignore[attr-defined]

    mcp = FastMCP(
        "polarion-2023",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        log_level=level,
    )

    for module in (projects, workitems, links, documents, testruns, misc):
        module.register(mcp, runtime)

    _register_prompts(mcp)
    _register_health_route(mcp, runtime)

    return mcp, runtime


def _register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(title="Review a Polarion requirement")
    def review_requirement(work_item_id: str) -> str:
        return (
            f"Review Polarion Work Item {work_item_id}.\n\n"
            "1. Call polarion_get_work_item with include_links=true.\n"
            "2. Check that the description states a single, verifiable requirement "
            "(actor, behaviour, measurable acceptance criteria).\n"
            "3. Report missing information, ambiguities and untestable wording.\n"
            "4. List linked and backlinked Work Items and say whether verification "
            "coverage exists (a 'verifies' link from a test case).\n"
            "Do not modify anything; propose the exact fields you would change and why."
        )

    @mcp.prompt(title="Find traceability gaps")
    def traceability_gaps(
        project_id: str = "", requirement_query: str = "type:requirement AND status:approved"
    ) -> str:
        project_hint = f" in project {project_id}" if project_id else ""
        return (
            f"Find traceability gaps for requirements{project_hint}.\n\n"
            f"1. Call polarion_search_work_items with query '{requirement_query}'.\n"
            "2. For each requirement, call polarion_get_linked_work_items and check for "
            "outgoing 'implements' links (design/code) and incoming 'verifies' links "
            "(test cases).\n"
            "3. Produce a table: requirement id | title | status | missing link role | "
            "candidate target.\n"
            "4. Summarise the count of requirements without verification coverage. "
            "Do not create links automatically; ask before writing."
        )

    @mcp.prompt(title="Prepare a test run from requirements")
    def prepare_test_run(
        project_id: str = "", scope_query: str = "type:requirement AND status:approved"
    ) -> str:
        project_hint = f" in project {project_id}" if project_id else ""
        return (
            f"Prepare a test run for requirements{project_hint}.\n\n"
            f"1. Call polarion_search_work_items with query '{scope_query}'.\n"
            "2. For each requirement, look for existing test cases via "
            "polarion_get_linked_work_items (incoming 'verifies'). Report gaps; do not "
            "invent test cases silently.\n"
            "3. Propose a test run title and the list of test case ids, then call "
            "polarion_create_test_run with dry_run=true.\n"
            "4. Show the preview and wait for approval before creating the run. "
            "Never record results that the user has not supplied."
        )


def _register_health_route(mcp: FastMCP, runtime: Runtime) -> None:
    try:
        from starlette.responses import JSONResponse
    except Exception:  # pragma: no cover - starlette ships with mcp
        return

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):  # type: ignore[no-untyped-def]
        caps = runtime.capabilities
        return JSONResponse(
            {
                "status": "ok",
                "ready": runtime.startup_error is None,
                "flavor": caps.flavor,
                "release": caps.release,
                "polarion_version": caps.polarion_version,
                "read_only": runtime.config.read_only,
            }
        )
