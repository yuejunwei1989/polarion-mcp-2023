"""Polarion version detection and capability matrix.

The open-source Polarion MCP servers assume a 2404+/2506+ server: they call
``GET /rest/v1/metadata`` during startup and expose every operation from the
current OpenAPI definition.  Polarion 2023 (2304 / 2310) has no ``/metadata``
endpoint and no custom-field-configuration, fields-metadata, license, jobs or
collections endpoints, so those servers fail before the first tool call.

This module models what is actually available on each release so the tool layer
can degrade instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any


def parse_release(version: str | None) -> int | None:
    """Turn a Polarion version string into a comparable ``YYMM`` integer.

    Accepts ``2310``, ``23.10``, ``2023.10``, ``2404.0``, ``Polarion 2506``,
    and strings containing a build suffix.
    """
    if not version:
        return None

    match = re.search(r"(?<!\d)(\d{4})[.\-](\d{1,2})(?!\d)", version)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 2000 <= year <= 2099 and 1 <= month <= 12:
            return (year % 100) * 100 + month

    match = re.search(r"(?<!\d)(\d{2})[.\-](\d{2})(?!\d)", version)
    if match:
        yy, mm = int(match.group(1)), int(match.group(2))
        if 20 <= yy <= 40 and 1 <= mm <= 12:
            return yy * 100 + mm

    match = re.search(r"(?<!\d)(\d{4})(?!\d)", version)
    if match:
        code = int(match.group(1))
        yy, mm = code // 100, code % 100
        if 20 <= yy <= 40 and 1 <= mm <= 12:
            return code
    return None


# Release thresholds for REST features.
FEATURE_MIN_RELEASE: dict[str, int] = {
    # 2304 ("2023.04") introduced the new high-throughput REST layer with
    # pagination, LiveDoc endpoints and enumerations.
    "projects": 2304,
    "work_items": 2304,
    "documents": 2304,
    "enumerations": 2304,
    "attachments": 2304,
    "comments": 2304,
    # 2310 ("2023.10") completed test management, plans and revisions.
    "test_runs": 2310,
    "plans": 2310,
    "revisions": 2310,
    "workflow_actions": 2310,
    "type_change": 2310,
    # 2404 added the discovery / admin surface.
    "metadata": 2404,
    "custom_field_config": 2404,
    "fields_metadata": 2404,
    "license": 2404,
    "jobs": 2404,
    "pages_crud": 2404,
    "bulk_backlink": 2404,
    "document_search": 2404,
    "document_part_move": 2404,
    # 2410 added collections.
    "collections": 2410,
}

FLAVOR_BASELINE = {
    "2023": 2310,
    "2025": 2606,
}


@dataclass(frozen=True)
class Capabilities:
    """Resolved feature flags for the connected server."""

    flavor: str
    release: int | None
    polarion_version: str | None = None
    build: str | None = None
    detected_by: str = "config"
    max_page_size: int = 100
    default_page_size: int = 100

    # feature flags
    projects: bool = True
    work_items: bool = True
    documents: bool = True
    document_search: bool = False
    document_part_move: bool = False
    enumerations: bool = True
    attachments: bool = True
    comments: bool = True
    test_runs: bool = True
    plans: bool = True
    revisions: bool = True
    workflow_actions: bool = True
    type_change: bool = True
    metadata: bool = False
    custom_field_config: bool = False
    fields_metadata: bool = False
    license: bool = False
    jobs: bool = False
    pages_crud: bool = False
    bulk_backlink: bool = False
    collections: bool = False

    def supports(self, feature: str) -> bool:
        return bool(getattr(self, feature, False))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def for_release(
        cls,
        release: int | None,
        *,
        flavor: str,
        polarion_version: str | None = None,
        build: str | None = None,
        detected_by: str = "config",
        max_page_size: int = 100,
        default_page_size: int = 100,
    ) -> "Capabilities":
        effective = release or FLAVOR_BASELINE.get(flavor, 2310)
        flags = {
            name: effective >= minimum for name, minimum in FEATURE_MIN_RELEASE.items()
        }
        return cls(
            flavor=flavor,
            release=effective,
            polarion_version=polarion_version,
            build=build,
            detected_by=detected_by,
            max_page_size=max_page_size,
            default_page_size=default_page_size,
            **flags,
        )
