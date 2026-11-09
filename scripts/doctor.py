#!/usr/bin/env python3
"""Read-only connectivity doctor for a real Polarion instance.

Usage:
    python scripts/doctor.py
    POLARION_URL=... POLARION_USERNAME=... POLARION_PASSWORD=... python scripts/doctor.py

It never writes to Polarion. Use it to confirm that the detected release,
credentials and endpoint shapes match your server before wiring the MCP client.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polarion_mcp_2023.client import PolarionClient  # noqa: E402
from polarion_mcp_2023.config import Config, ConfigError  # noqa: E402
from polarion_mcp_2023.errors import PolarionError  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.getenv("NO_COLOR"):
    GREEN = RED = YELLOW = DIM = RESET = ""


def line(status: str, label: str, detail: str = "") -> None:
    color = {"ok": GREEN, "fail": RED, "warn": YELLOW}.get(status, DIM)
    mark = {"ok": "PASS", "fail": "FAIL", "warn": "WARN"}.get(status, "INFO")
    print(f"{color}[{mark}]{RESET} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"{RED}Configuration error:{RESET} {exc}")
        return 2

    print(f"Polarion URL : {config.base_url}")
    print(f"REST API     : {config.rest_url}")
    print(f"Auth mode    : {config.resolved_auth_mode}")
    print(f"Default proj : {config.default_project or '(none)'}")
    print("-" * 72)

    client = PolarionClient(config)
    failures = 0

    try:
        caps = client.probe()
    except PolarionError as exc:
        line("fail", "REST API reachable / credentials valid", exc.render())
        if exc.status == 401:
            print(
                f"{DIM}Check the username/password, or that the REST API is enabled in "
                f"Administration > Global > REST API.{RESET}"
            )
        elif exc.status == 404:
            print(
                f"{DIM}POLARION_URL is probably wrong. Expected <server>/polarion, "
                f"got {config.base_url}.{RESET}"
            )
        return 1

    line(
        "ok",
        "REST API reachable / credentials valid",
        f"flavour={caps.flavor} release={caps.release} detected_by={caps.detected_by}",
    )
    if caps.polarion_version:
        line("ok", "Reported Polarion version", caps.polarion_version)
    else:
        line("warn", "Version endpoint unavailable", "normal on 2023 (no /metadata endpoint)")

    try:
        projects = client.get_all(
            "projects", params={"fields[projects]": "@basic"}, max_items=50
        )
        line("ok", "List projects", f"{len(projects)} visible")
    except PolarionError as exc:
        projects = []
        failures += 1
        line("fail", "List projects", exc.render())

    project_id = config.default_project or (
        str(projects[0].get("id")) if projects else None
    )
    if project_id:
        try:
            project = client.get(f"projects/{project_id}")
            name = ((project.get("data") or {}).get("attributes") or {}).get("name")
            line("ok", f"Read project {project_id}", str(name or ""))
        except PolarionError as exc:
            failures += 1
            line("fail", f"Read project {project_id}", exc.render())

        checks = {
            "Work items": f"projects/{project_id}/workitems",
            "Spaces": f"projects/{project_id}/spaces",
            "Test runs": f"projects/{project_id}/testruns",
            "Enumerations": f"projects/{project_id}/enumerations",
        }
        for label, path in checks.items():
            try:
                payload = client.get(path, params={"page[size]": 1})
                count = len(payload.get("data") or []) if isinstance(payload, dict) else 0
                line("ok", label, f"first page returned {count} row(s)")
            except PolarionError as exc:
                if exc.status in {403, 404, 405}:
                    line("warn", label, exc.render())
                else:
                    failures += 1
                    line("fail", label, exc.render())

    print("-" * 72)
    if failures:
        print(f"{RED}{failures} check(s) failed.{RESET} Fix these before using write tools.")
        return 1
    print(f"{GREEN}All critical checks passed.{RESET}")
    print(
        f"{DIM}Next: set POLARION_READ_ONLY=1 for an initial read-only MCP session, "
        f"then run polarion_server_info from your AI client.{RESET}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise SystemExit(3)
