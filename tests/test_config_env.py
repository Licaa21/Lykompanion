"""Tests for the .env persistence helpers in app/core/config.py."""

import pytest

from app.core import config


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")


def test_appends_new_keys():
    config.persist_env_values({"FOO": "bar", "BAZ": "qux"})
    content = config.ENV_PATH.read_text(encoding="utf-8")
    assert "FOO=bar\n" in content
    assert "BAZ=qux\n" in content


def test_updates_existing_key_preserving_others():
    config.ENV_PATH.write_text("FOO=old\nKEEP=untouched\n", encoding="utf-8")
    config.persist_env_values({"FOO": "new"})
    lines = config.ENV_PATH.read_text(encoding="utf-8").splitlines()
    assert lines == ["FOO=new", "KEEP=untouched"]


def test_newlines_in_values_cannot_corrupt_the_file():
    config.persist_env_values({"KEY": "line1\nline2\rline3"})
    lines = [l for l in config.ENV_PATH.read_text(encoding="utf-8").splitlines() if l]
    assert lines == ["KEY=line1 line2 line3"]


def test_prefix_key_is_not_mistaken_for_match():
    config.ENV_PATH.write_text("FOOBAR=1\n", encoding="utf-8")
    config.persist_env_values({"FOO": "2"})
    lines = config.ENV_PATH.read_text(encoding="utf-8").splitlines()
    assert "FOOBAR=1" in lines
    assert "FOO=2" in lines
