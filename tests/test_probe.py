from __future__ import annotations

import pytest

from polarion_mcp_2023.capabilities import Capabilities, parse_release
from polarion_mcp_2023.client import PolarionClient
from polarion_mcp_2023.errors import PolarionError


def test_parse_release_variants():
    assert parse_release("2310") == 2310
    assert parse_release("23.10") == 2310
    assert parse_release("2023.10") == 2310
    assert parse_release("Polarion 2506 build 123") == 2506
    assert parse_release(None) is None


def test_capability_matrix_for_2023():
    caps = Capabilities.for_release(2310, flavor="2023")
    assert caps.work_items and caps.documents and caps.test_runs and caps.workflow_actions
    assert not caps.metadata
    assert not caps.custom_field_config
    assert not caps.fields_metadata
    assert not caps.license
    assert not caps.jobs
    assert not caps.collections
    assert not caps.bulk_backlink


def test_capability_matrix_for_2404_and_later():
    caps = Capabilities.for_release(2404, flavor="2025")
    assert caps.metadata and caps.custom_field_config and caps.fields_metadata and caps.jobs
    assert not caps.collections
    assert Capabilities.for_release(2410, flavor="2025").collections


def test_probe_detects_2023_without_metadata(runtime):
    caps = runtime.capabilities
    assert caps.flavor == "2023"
    assert caps.release == 2310
    assert caps.detected_by == "metadata-absent"
    assert caps.metadata is False


def test_forcing_2025_flavour_fails_on_2023(config):
    config = type(config)(**{**config.__dict__, "api_flavor": "2025"})
    client = PolarionClient(config)
    with pytest.raises(PolarionError) as exc:
        client.probe()
    assert exc.value.status == 404
    assert "metadata" in str(exc.value).lower()


def test_project_capabilities_probe(runtime):
    caps = runtime.client.probe_project("DEMO")
    assert caps["documents"] is True
    assert caps["test_runs"] is True
    assert caps["enumerations"] is True
    # The fake 2310 server has no plans endpoint, so the probe degrades gracefully.
    assert caps["plans"] is False


def test_probe_uses_basic_auth_and_rejects_bad_password(config_factory):
    client = PolarionClient(config_factory(password="wrong"))
    with pytest.raises(PolarionError) as exc:
        client.probe()
    assert exc.value.status == 401
    assert "hint" in exc.value.to_dict()


def test_token_auth(config_factory):
    config = config_factory(
        username=None, password=None, token="test-token", auth_mode="token"
    )
    caps = PolarionClient(config).probe()
    assert caps.flavor == "2023"
