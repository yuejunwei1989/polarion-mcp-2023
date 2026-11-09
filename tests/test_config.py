from __future__ import annotations

import pytest

from polarion_mcp_2023.config import Config, ConfigError, normalize_base_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://p.example.com", "https://p.example.com/polarion"),
        ("https://p.example.com/", "https://p.example.com/polarion"),
        ("https://p.example.com/polarion", "https://p.example.com/polarion"),
        ("https://p.example.com/polarion/", "https://p.example.com/polarion"),
        ("https://p.example.com/polarion/rest/v1", "https://p.example.com/polarion"),
        ("https://p.example.com/polarion/rest", "https://p.example.com/polarion"),
        ("http://host:8080/custom/polarion/rest/v1/", "http://host:8080/custom/polarion"),
    ],
)
def test_normalize_base_url(raw, expected):
    assert normalize_base_url(raw) == expected


def test_rejects_relative_url():
    with pytest.raises(ConfigError):
        normalize_base_url("polarion.example.com")


def _clear_env(monkeypatch):
    for name in (
        "POLARION_URL",
        "POLARION_USERNAME",
        "POLARION_PASSWORD",
        "POLARION_TOKEN",
        "POLARION_AUTH_MODE",
        "POLARION_API_FLAVOR",
        "POLARION_DEFAULT_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_requires_credentials(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("POLARION_URL", "https://p.example.com")
    with pytest.raises(ConfigError, match="No credentials"):
        Config.from_env()


def test_requires_url(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("POLARION_TOKEN", "abc")
    with pytest.raises(ConfigError, match="POLARION_URL"):
        Config.from_env()


def test_basic_auth_mode(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("POLARION_URL", "https://p.example.com")
    monkeypatch.setenv("POLARION_USERNAME", "alice")
    monkeypatch.setenv("POLARION_PASSWORD", "secret")
    config = Config.from_env()
    assert config.resolved_auth_mode == "basic"
    assert config.rest_url == "https://p.example.com/polarion/rest/v1"


def test_token_auth_wins_in_auto_mode(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("POLARION_URL", "https://p.example.com")
    monkeypatch.setenv("POLARION_USERNAME", "alice")
    monkeypatch.setenv("POLARION_PASSWORD", "secret")
    monkeypatch.setenv("POLARION_TOKEN", "pat-123")
    assert Config.from_env().resolved_auth_mode == "token"


def test_explicit_basic_mode_requires_password(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("POLARION_URL", "https://p.example.com")
    monkeypatch.setenv("POLARION_AUTH_MODE", "basic")
    monkeypatch.setenv("POLARION_TOKEN", "pat-123")
    with pytest.raises(ConfigError, match="POLARION_AUTH_MODE=basic"):
        Config.from_env()
