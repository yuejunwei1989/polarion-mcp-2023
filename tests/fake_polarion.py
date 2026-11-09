"""A small in-memory Polarion 2310 look-alike used by the test suite.

It deliberately behaves like a 2023 server:

* ``GET /rest/v1/metadata`` returns 404 (the endpoint arrived in 2404)
* ``GET /rest/v1/projects/{id}/documents`` returns 404 (project-wide document
  listing arrived in 2404); callers must walk spaces
* Basic authentication and PAT bearer tokens are both accepted
* errors use the JSON:API ``errors`` array

Run standalone with ``python tests/fake_polarion.py --port 8899``.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Tuple
from urllib.parse import parse_qs, unquote, urlparse

API_PREFIX = "/polarion/rest/v1"
USERNAME = "alice"
PASSWORD = "secret"
TOKEN = "test-token"


def _doc(type_: str, id_: str, attributes: dict[str, Any], relationships: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": type_, "id": id_, "attributes": attributes}
    if relationships:
        payload["relationships"] = relationships
    payload["links"] = {"self": f"http://polarion.test/polarion/rest/v1/{type_}/{id_}"}
    return payload


class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.projects = {
            "DEMO": _doc(
                "projects",
                "DEMO",
                {
                    "name": "Demo Project",
                    "description": "A project for tests",
                    "active": True,
                    "status": "active",
                    "lead": "alice",
                },
                {
                    "lead": {"data": {"type": "users", "id": "alice"}},
                    "workItemTypes": {
                        "data": [
                            {"type": "workitemtypes", "id": "requirement"},
                            {"type": "workitemtypes", "id": "task"},
                            {"type": "workitemtypes", "id": "defect"},
                        ]
                    },
                },
            ),
            "OTHER": _doc(
                "projects",
                "OTHER",
                {"name": "Other Project", "active": True, "status": "active"},
            ),
        }
        self.work_items: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []
        self.next_id = 1
        self.spaces = {
            "DEMO": [
                _doc("spaces", "Specification", {"name": "Specification"}),
                _doc("spaces", "Design", {"name": "Design"}),
            ]
        }
        self.documents = {
            ("DEMO", "Specification"): [
                _doc(
                    "documents",
                    "System Requirements",
                    {
                        "title": "System Requirements",
                        "type": "document",
                        "status": "draft",
                        "homePageContent": "<h1>System Requirements</h1><p>Power supply.</p>",
                    },
                ),
                _doc("documents", "Interface Spec", {"title": "Interface Spec"}),
            ]
        }
        self.parts = {
            ("DEMO", "Specification", "System Requirements"): [
                _doc(
                    "documentparts",
                    "part-1",
                    {"title": "1 Introduction", "level": 1, "outlineNumber": "1", "content": "<p>Intro</p>"},
                ),
                _doc(
                    "documentparts",
                    "part-2",
                    {"title": "2 Requirements", "level": 1, "outlineNumber": "2", "content": "<p>Req</p>"},
                    {"workItem": {"data": {"type": "workitems", "id": "DEMO-1"}}},
                ),
            ]
        }
        self.test_runs: dict[str, dict[str, Any]] = {}
        self.test_records: dict[str, list[dict[str, Any]]] = {}
        self.next_run = 1
        self.enumerations = {
            "status": _doc(
                "enumerations",
                "status",
                {
                    "name": "Status",
                    "options": [
                        {"id": "open", "name": "Open", "default": True},
                        {"id": "inProgress", "name": "In Progress"},
                        {"id": "closed", "name": "Closed"},
                    ],
                },
            ),
            "severity": _doc(
                "enumerations",
                "severity",
                {"name": "Severity", "options": [{"id": "minor"}, {"id": "major"}]},
            ),
        }
        self.users = [
            _doc("users", "alice", {"name": "Alice", "email": "alice@example.com"}),
            _doc("users", "bob", {"name": "Bob", "email": "bob@example.com"}),
        ]
        self.comments: dict[str, list[dict[str, Any]]] = {}

        self.add_work_item(
            title="Power supply requirement",
            type_="requirement",
            status="open",
            severity="major",
            description="<p>The unit shall provide 24 V.</p>",
            custom_fields={"safetyLevel": "ASIL-B"},
        )
        self.add_work_item(title="Implement power supply", type_="task", status="inProgress")
        self.add_work_item(title="Loose connector", type_="defect", status="open", severity="minor")
        self.links.append(
            {
                "id": "DEMO-2|implements|DEMO-1",
                "source": "DEMO-2",
                "target": "DEMO-1",
                "role": "implements",
                "suspect": False,
            }
        )

    def add_work_item(self, **kwargs: Any) -> dict[str, Any]:
        item_id = f"DEMO-{self.next_id}"
        self.next_id += 1
        attributes = {
            "title": kwargs.get("title", "Untitled"),
            "type": kwargs.get("type_", "task"),
            "status": kwargs.get("status", "open"),
            "description": kwargs.get("description", ""),
            "created": "2024-01-01T10:00:00Z",
            "updated": "2024-01-02T10:00:00Z",
        }
        if kwargs.get("severity"):
            attributes["severity"] = kwargs["severity"]
        attributes.update(kwargs.get("custom_fields") or {})
        item = _doc(
            "workitems",
            item_id,
            attributes,
            {
                "project": {"data": {"type": "projects", "id": "DEMO"}},
                "type": {"data": {"type": "workitemtypes", "id": attributes["type"]}},
                "status": {"data": {"type": "enumerations", "id": attributes["status"]}},
                "author": {"data": {"type": "users", "id": "alice"}},
                "assignee": {"data": [{"type": "users", "id": "bob"}]},
            },
        )
        self.work_items[item_id] = item
        return item

    def render_work_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = item["id"]
        copy = json.loads(json.dumps(item))
        copy["relationships"]["linkedWorkItems"] = {
            "data": [
                {"type": "workitems", "id": link["target"]}
                for link in self.links
                if link["source"] == item_id
            ]
        }
        return copy


def _lucene_match(item: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    query = query.strip()
    attributes = item.get("attributes") or {}

    id_match = re.match(r"^id:\((.+)\)$", query)
    if id_match:
        wanted = {
            token.strip().strip('"')
            for token in re.split(r"\s+OR\s+", id_match.group(1))
            if token.strip()
        }
        return item["id"] in wanted

    for clause in re.split(r"\s+AND\s+", query):
        clause = clause.strip()
        if not clause:
            continue
        if clause.startswith("NOT "):
            field, _, value = clause[4:].partition(":")
            if field and str(attributes.get(field, "")) == value.strip('"'):
                return False
            continue
        field, _, value = clause.partition(":")
        if not _:
            # bare term: match title
            if clause.strip('"').lower() not in str(attributes.get("title", "")).lower():
                return False
            continue
        value = value.strip().strip('"')
        actual = str(attributes.get(field, ""))
        if actual != value:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "FakePolarion/2310"
    store: Store

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    # ------------------------------------------------------------- utilities
    def _auth_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:] == TOKEN
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode()
            except Exception:
                return False
            return decoded == f"{USERNAME}:{PASSWORD}"
        return False

    def _send(self, status: int, payload: Any = None, content_type: str = "application/vnd.api+json") -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status: int, title: str, detail: str) -> None:
        self._send(
            status,
            {"errors": [{"status": str(status), "title": title, "detail": detail}]},
        )

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _paginate(self, rows: list[dict[str, Any]], query: dict[str, list[str]]) -> dict[str, Any]:
        size = int(query.get("page[size]", ["100"])[0])
        number = int(query.get("page[number]", ["1"])[0])
        size = max(1, min(size, 100))
        start = (number - 1) * size
        chunk = rows[start : start + size]
        return {"data": chunk, "meta": {"totalCount": len(rows)}}

    # ---------------------------------------------------------------- routes
    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._error(401, "Unauthorized", "Valid credentials are required")
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith(API_PREFIX):
            self._error(404, "Not Found", "Unknown context path")
            return
        path = unquote(parsed.path[len(API_PREFIX) :]).strip("/")
        query = parse_qs(parsed.query)
        parts = path.split("/") if path else []
        store = self.store

        # Metadata is intentionally absent on 2310.
        if path == "metadata":
            self._error(404, "Not Found", "No resource matched the request")
            return

        if path == "projects":
            rows = list(store.projects.values())
            self._send(200, self._paginate(rows, query))
            return

        if len(parts) == 2 and parts[0] == "projects":
            project = store.projects.get(parts[1])
            if not project:
                self._error(404, "Not Found", f"Project {parts[1]} not found")
                return
            self._send(200, {"data": project})
            return

        if len(parts) >= 3 and parts[0] == "projects" and parts[2] == "workitems":
            project_id = parts[1]
            if len(parts) == 3:
                rows = [
                    store.render_work_item(item)
                    for item in store.work_items.values()
                    if ((item.get("relationships") or {}).get("project", {}).get("data") or {}).get("id")
                    == project_id
                ]
                lucene = query.get("query", [""])[0]
                rows = [row for row in rows if _lucene_match(row, lucene)]
                self._send(200, self._paginate(rows, query))
                return

            item_id = parts[3]
            item = store.work_items.get(item_id)
            if not item:
                self._error(404, "Not Found", f"Work Item {item_id} not found")
                return
            suffix = "/".join(parts[4:])
            if not suffix:
                self._send(200, {"data": store.render_work_item(item)})
                return
            if suffix == "linkedworkitems":
                rows = [
                    _doc(
                        "linkedworkitems",
                        link["id"],
                        {"role": link["role"], "suspect": link["suspect"]},
                        {
                            "source": {"data": {"type": "workitems", "id": link["source"]}},
                            "target": {"data": {"type": "workitems", "id": link["target"]}},
                        },
                    )
                    for link in store.links
                    if link["source"] == item_id
                ]
                self._send(200, {"data": rows})
                return
            if suffix == "backlinkedworkitems":
                rows = [
                    _doc(
                        "linkedworkitems",
                        link["id"],
                        {"role": link["role"], "suspect": link["suspect"]},
                        {
                            "source": {"data": {"type": "workitems", "id": link["source"]}},
                            "target": {"data": {"type": "workitems", "id": link["target"]}},
                        },
                    )
                    for link in store.links
                    if link["target"] == item_id
                ]
                self._send(200, {"data": rows})
                return
            if suffix == "revisions":
                rows = [
                    _doc(
                        "revisions",
                        f"{item_id}-r1",
                        {
                            "author": "alice",
                            "created": "2024-01-01T10:00:00Z",
                            "message": "Created",
                        },
                    )
                ]
                self._send(200, {"data": rows})
                return
            if suffix == "comments":
                self._send(200, {"data": store.comments.get(item_id, [])})
                return
            if suffix == "actions":
                self._send(
                    200,
                    {
                        "data": [
                            _doc(
                                "workflowactions",
                                "start",
                                {"name": "Start Progress", "targetStatus": "inProgress"},
                            )
                        ]
                    },
                )
                return
            if suffix == "attachments":
                self._send(200, {"data": []})
                return
            self._error(404, "Not Found", f"Unsupported Work Item sub-resource {suffix}")
            return

        if len(parts) >= 3 and parts[0] == "projects" and parts[2] == "spaces":
            project_id = parts[1]
            if len(parts) == 3:
                self._send(200, {"data": store.spaces.get(project_id, [])})
                return
            space_id = parts[3]
            if len(parts) == 4:
                self._error(404, "Not Found", "Document collection requires a space")
                return
            if parts[4] == "documents":
                if len(parts) == 5:
                    rows = store.documents.get((project_id, space_id), [])
                    self._send(200, {"data": rows})
                    return
                document_name = parts[5]
                documents = store.documents.get((project_id, space_id), [])
                document = next(
                    (entry for entry in documents if entry["id"] == document_name), None
                )
                if not document:
                    self._error(404, "Not Found", f"Document {document_name} not found")
                    return
                if len(parts) == 6:
                    self._send(200, {"data": document})
                    return
                if parts[6] == "parts":
                    if len(parts) == 7:
                        self._send(
                            200,
                            {"data": store.parts.get((project_id, space_id, document_name), [])},
                        )
                        return
                    part_id = parts[7]
                    part = next(
                        (
                            entry
                            for entry in store.parts.get((project_id, space_id, document_name), [])
                            if entry["id"] == part_id
                        ),
                        None,
                    )
                    if not part:
                        self._error(404, "Not Found", f"Part {part_id} not found")
                        return
                    self._send(200, {"data": part})
                    return
            self._error(404, "Not Found", "Unsupported document path")
            return

        # Project-wide document listing arrived in 2404, not 2310.
        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "documents":
            self._error(404, "Not Found", "No resource matched the request")
            return

        if len(parts) >= 3 and parts[0] == "projects" and parts[2] == "testruns":
            project_id = parts[1]
            if len(parts) == 3:
                rows = list(store.test_runs.values())
                lucene = query.get("query", [""])[0]
                rows = [row for row in rows if _lucene_match(row, lucene)]
                self._send(200, self._paginate(rows, query))
                return
            run_id = parts[3]
            run = store.test_runs.get(run_id)
            if not run:
                self._error(404, "Not Found", f"Test Run {run_id} not found")
                return
            if len(parts) == 4:
                self._send(200, {"data": run})
                return
            if parts[4] == "testrecords":
                self._send(200, {"data": store.test_records.get(run_id, [])})
                return
            self._error(404, "Not Found", "Unsupported test run path")
            return

        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "enumerations":
            self._send(200, {"data": list(store.enumerations.values())})
            return
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "enumerations":
            enumeration = store.enumerations.get(parts[3])
            if not enumeration:
                self._error(404, "Not Found", f"Enumeration {parts[3]} not found")
                return
            self._send(200, {"data": enumeration})
            return

        if path == "users":
            self._send(200, self._paginate(store.users, query))
            return

        self._error(404, "Not Found", f"No resource matched /{path}")

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._error(401, "Unauthorized", "Valid credentials are required")
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path[len(API_PREFIX) :]).strip("/")
        parts = path.split("/") if path else []
        store = self.store
        body = self._read_body()

        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "workitems":
            document = (body or {}).get("data")
            if isinstance(document, list):
                document = document[0] if document else {}
            attributes = dict(document.get("attributes") or {})
            if not attributes.get("title"):
                self._error(422, "Unprocessable Entity", "title is required")
                return
            item = store.add_work_item(
                title=attributes.pop("title"),
                type_=attributes.pop("type", "task"),
                status=attributes.pop("status", "open"),
                description=attributes.pop("description", ""),
                custom_fields=attributes,
            )
            self._send(201, {"data": [item]})
            return

        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "workitems":
            item_id, action = parts[3], parts[4]
            if item_id not in store.work_items:
                self._error(404, "Not Found", f"Work Item {item_id} not found")
                return
            if action == "linkedworkitems":
                document = (body or {}).get("data")
                if isinstance(document, list):
                    document = document[0] if document else {}
                attributes = document.get("attributes") or {}
                target = ((document.get("relationships") or {}).get("target") or {}).get("data")
                if not attributes.get("role") or not isinstance(target, dict):
                    self._error(422, "Unprocessable Entity", "role and target are required")
                    return
                link = {
                    "id": f"{item_id}|{attributes['role']}|{target['id']}",
                    "source": item_id,
                    "target": target["id"],
                    "role": attributes["role"],
                    "suspect": attributes.get("suspect", False),
                }
                store.links.append(link)
                self._send(201, {"data": [_doc("linkedworkitems", link["id"], attributes)]})
                return
            if action == "comments":
                document = (body or {}).get("data")
                if isinstance(document, list):
                    document = document[0] if document else {}
                comment = _doc(
                    "comments",
                    f"c{len(store.comments.get(item_id, [])) + 1}",
                    {
                        "text": (document.get("attributes") or {}).get("text"),
                        "created": "2024-02-01T10:00:00Z",
                        "author": "alice",
                    },
                )
                store.comments.setdefault(item_id, []).append(comment)
                self._send(201, {"data": [comment]})
                return
            if action == "actions":
                self._send(200, {"data": []})
                return
            self._error(404, "Not Found", f"Unsupported action {action}")
            return

        if len(parts) == 3 and parts[0] == "projects" and parts[2] == "testruns":
            document = (body or {}).get("data")
            if isinstance(document, list):
                document = document[0] if document else {}
            attributes = dict(document.get("attributes") or {})
            run_id = f"TR-{store.next_run}"
            store.next_run += 1
            run = _doc(
                "testruns",
                run_id,
                {
                    "title": attributes.get("title", "Test Run"),
                    "type": attributes.get("type", "manual"),
                    "status": "open",
                    "isTemplate": False,
                },
            )
            store.test_runs[run_id] = run
            store.test_records[run_id] = []
            self._send(201, {"data": [run]})
            return

        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "testruns" and parts[4] == "testrecords":
            run_id = parts[3]
            if run_id not in store.test_runs:
                self._error(404, "Not Found", f"Test Run {run_id} not found")
                return
            document = (body or {}).get("data")
            if isinstance(document, list):
                document = document[0] if document else {}
            attributes = document.get("attributes") or {}
            if not attributes.get("result"):
                self._error(422, "Unprocessable Entity", "result is required")
                return
            test_case = ((document.get("relationships") or {}).get("testCase") or {}).get("data")
            record = _doc(
                "testrecords",
                f"REC-{len(store.test_records[run_id]) + 1}",
                attributes,
                {"testCase": {"data": {"type": "workitems", "id": (test_case or {}).get("id")}}},
            )
            store.test_records[run_id].append(record)
            self._send(201, {"data": [record]})
            return

        self._error(404, "Not Found", f"No resource matched /{path}")

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._error(401, "Unauthorized", "Valid credentials are required")
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path[len(API_PREFIX) :]).strip("/")
        parts = path.split("/")
        body = self._read_body()
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "workitems":
            item_id = parts[3]
            item = self.store.work_items.get(item_id)
            if not item:
                self._error(404, "Not Found", f"Work Item {item_id} not found")
                return
            document = (body or {}).get("data") or {}
            unknown = [
                key
                for key in (document.get("attributes") or {})
                if key in {"unknownField", "invalid"}
            ]
            if unknown:
                self._error(422, "Unprocessable Entity", f"Unknown field {unknown[0]}")
                return
            item["attributes"].update(document.get("attributes") or {})
            item["attributes"]["updated"] = "2024-03-01T10:00:00Z"
            relationships = document.get("relationships") or {}
            if "assignee" in relationships:
                assignees = (relationships["assignee"] or {}).get("data") or []
                item["relationships"]["assignee"] = {"data": assignees}
            self._send(200, {"data": self.store.render_work_item(item)})
            return
        self._error(404, "Not Found", f"No resource matched /{path}")

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._error(401, "Unauthorized", "Valid credentials are required")
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path[len(API_PREFIX) :]).strip("/")
        parts = path.split("/")
        if len(parts) == 4 and parts[0] == "projects" and parts[2] == "workitems":
            item_id = parts[3]
            if item_id not in self.store.work_items:
                self._error(404, "Not Found", f"Work Item {item_id} not found")
                return
            del self.store.work_items[item_id]
            self._send(204)
            return
        if len(parts) == 5 and parts[0] == "projects" and parts[2] == "workitems" and parts[4] == "linkedworkitems":
            self._error(404, "Not Found", "Use the target-id path")
            return
        if len(parts) == 6 and parts[0] == "projects" and parts[2] == "workitems" and parts[4] == "linkedworkitems":
            item_id, target = parts[3], parts[5]
            before = len(self.store.links)
            self.store.links = [
                link
                for link in self.store.links
                if not (link["source"] == item_id and link["target"] == target)
            ]
            if len(self.store.links) == before:
                self._error(404, "Not Found", "Link not found")
                return
            self._send(204)
            return
        self._error(404, "Not Found", f"No resource matched /{path}")


def create_server(port: int = 0, verbose: bool = False) -> Tuple[ThreadingHTTPServer, Store]:
    store = Store()
    handler = type("BoundHandler", (Handler,), {"store": store})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.verbose = verbose  # type: ignore[attr-defined]
    return server, store


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake Polarion 2310 REST server")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    server, _ = create_server(args.port, args.verbose)
    print(f"Fake Polarion 2310 listening on http://127.0.0.1:{args.port}/polarion/rest/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
