"""Test management tools (Polarion 2310+ has full Test Run CRUD over REST)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..errors import PolarionError
from ..normalize import normalize_test_record, normalize_test_run
from ..runtime import Runtime, first_item, ok, require_id, tool


def register(mcp: Any, runtime: Runtime) -> None:
    client = runtime.client

    @mcp.tool()
    @tool
    def polarion_list_test_runs(
        project_id: str | None = None,
        query: str | None = None,
        limit: int = 50,
        include_templates: bool = False,
    ) -> dict[str, Any]:
        """List Test Runs in a project.

        Args:
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            query: Optional Lucene query, e.g. 'status:inProgress' or 'title:"Release 2"'.
            limit: Maximum test runs to return.
            include_templates: Include Test Run templates (excluded by default).
        """
        pid = runtime.project(project_id)
        limit = max(1, min(int(limit), 300))
        params: dict[str, Any] = {"fields[testruns]": "@all"}
        if query and query.strip():
            params["query"] = query.strip()

        raw = client.get_all(
            f"projects/{pid}/testruns",
            params=params,
            max_items=limit + 20 if not include_templates else limit,
        )
        runs = [normalize_test_run(item) for item in raw]
        if not include_templates:
            runs = [run for run in runs if not run.get("isTemplate")]
        return ok(runs[:limit], project=pid, count=min(len(runs), limit))

    @mcp.tool()
    @tool
    def polarion_get_test_run(
        test_run_id: str,
        project_id: str | None = None,
        include_records: bool = True,
        record_limit: int = 300,
    ) -> dict[str, Any]:
        """Get a Test Run with an optional pass/fail summary of its test records.

        Args:
            test_run_id: Test Run id.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            include_records: Include individual test records and a result summary.
            record_limit: Maximum test records to read.
        """
        pid = runtime.project(project_id)
        run_id = require_id(test_run_id, "test_run_id")
        raw = client.get(f"projects/{pid}/testruns/{run_id}", params={"fields[testruns]": "@all"})
        data = normalize_test_run(first_item(raw) or {"id": run_id})

        if include_records:
            try:
                records_raw = client.get_all(
                    f"projects/{pid}/testruns/{run_id}/testrecords",
                    params={"fields[testrecords]": "@all"},
                    max_items=max(1, min(int(record_limit), 1000)),
                )
                records = [normalize_test_record(item) for item in records_raw]
                data["records"] = records
                data["summary"] = dict(Counter(r.get("result", "unknown") for r in records))
            except PolarionError as exc:
                if exc.status not in {404, 405}:
                    raise
                data["records_error"] = exc.to_dict()
        return ok(data, project=pid)

    @mcp.tool()
    @tool
    def polarion_create_test_run(
        title: str,
        project_id: str | None = None,
        template_id: str | None = None,
        test_run_type: str = "manual",
        planned_start: str | None = None,
        planned_end: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a Test Run. Requires write access.

        Args:
            title: Test Run title.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            template_id: Optional Test Run Template Work Item id.
            test_run_type: 'manual' (default) or 'automated'.
            planned_start: ISO-8601 date or datetime.
            planned_end: ISO-8601 date or datetime.
            dry_run: Build the request without sending it.
        """
        runtime.ensure_write()
        pid = runtime.project(project_id)
        attributes: dict[str, Any] = {
            "title": require_id(title, "title"),
            "type": test_run_type or "manual",
        }
        if planned_start:
            attributes["plannedStart"] = planned_start
        if planned_end:
            attributes["plannedEnd"] = planned_end

        document: dict[str, Any] = {"type": "testruns", "attributes": attributes}
        if template_id:
            document["relationships"] = {
                "template": {"data": {"type": "workitems", "id": template_id}}
            }
        body = {"data": [document]}
        path = f"projects/{pid}/testruns"
        if dry_run:
            return runtime.dry_run("POST", path, body)

        created = first_item(client.post(path, json_body=body))
        if created is None:
            raise ValueError("Polarion accepted the request but returned no Test Run")
        return ok(normalize_test_run(created), project=pid, created=True)

    @mcp.tool()
    @tool
    def polarion_add_test_record(
        test_run_id: str,
        test_case_id: str,
        result: str,
        project_id: str | None = None,
        comment: str | None = None,
        executed: str | None = None,
        duration: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record a test result for a Test Case in a Test Run. Requires write access.

        Args:
            test_run_id: Test Run id.
            test_case_id: Test Case Work Item id.
            result: Result id, typically 'passed', 'failed', 'blocked' or 'notApplicable'.
                Valid values are project-configurable; check the project's enumeration.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            comment: Optional execution comment.
            executed: ISO-8601 timestamp; defaults to server time when omitted.
            duration: Optional duration, e.g. 'PT5M30S' (ISO-8601) or '330'.
            dry_run: Build the request without sending it.
        """
        runtime.ensure_write()
        pid = runtime.project(project_id)
        run_id = require_id(test_run_id, "test_run_id")
        case_id = require_id(test_case_id, "test_case_id")
        result_id = require_id(result, "result")

        attributes: dict[str, Any] = {"result": result_id}
        if comment is not None:
            attributes["comment"] = comment
        if executed:
            attributes["executed"] = executed
        if duration:
            attributes["duration"] = duration

        body = {
            "data": [
                {
                    "type": "testrecords",
                    "attributes": attributes,
                    "relationships": {
                        "testCase": {"data": {"type": "workitems", "id": case_id}}
                    },
                }
            ]
        }
        path = f"projects/{pid}/testruns/{run_id}/testrecords"
        if dry_run:
            return runtime.dry_run("POST", path, body)

        created = first_item(client.post(path, json_body=body))
        record = normalize_test_record(created) if created else {"result": result_id}
        return ok(record, project=pid, test_run=run_id, created=True)

    @mcp.tool()
    @tool
    def polarion_get_test_records(
        test_run_id: str,
        project_id: str | None = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        """List test records of a Test Run with a result summary.

        Args:
            test_run_id: Test Run id.
            project_id: Project id. Defaults to POLARION_DEFAULT_PROJECT.
            limit: Maximum records to return.
        """
        pid = runtime.project(project_id)
        run_id = require_id(test_run_id, "test_run_id")
        raw = client.get_all(
            f"projects/{pid}/testruns/{run_id}/testrecords",
            params={"fields[testrecords]": "@all"},
            max_items=max(1, min(int(limit), 1000)),
        )
        records = [normalize_test_record(item) for item in raw]
        return ok(
            records,
            project=pid,
            test_run=run_id,
            count=len(records),
            summary=dict(Counter(record.get("result", "unknown") for record in records)),
        )
