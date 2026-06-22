"""TDD tests for mcg_swarm.env.load_dotenv — RED phase first, then GREEN."""
import os
import tempfile
import pytest
from mcg_swarm.env import load_dotenv


def test_space_and_quoted_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('ANTHROPIC_API_KEY= "abc123"\n')
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("ANTHROPIC_API_KEY") == "abc123"


def test_no_space_quoted_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('MY_KEY="hello"\n')
    monkeypatch.delenv("MY_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("MY_KEY") == "hello"


def test_unquoted_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('PLAIN_KEY=plain_value\n')
    monkeypatch.delenv("PLAIN_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("PLAIN_KEY") == "plain_value"


def test_single_quoted_value(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SINGLE_KEY='single_val'\n")
    monkeypatch.delenv("SINGLE_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("SINGLE_KEY") == "single_val"


def test_missing_file_noop():
    # Should not raise
    load_dotenv("/nonexistent/path/.env")


def test_does_not_overwrite_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('EXISTING_KEY="from_file"\n')
    monkeypatch.setenv("EXISTING_KEY", "from_env")
    load_dotenv(str(env_file))
    assert os.environ.get("EXISTING_KEY") == "from_env"


def test_ignores_blank_lines_and_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# This is a comment\n"
        "\n"
        'REAL_KEY="realval"\n'
        "# Another comment\n"
    )
    monkeypatch.delenv("REAL_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("REAL_KEY") == "realval"


def test_no_trailing_newline(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    # write without trailing newline
    env_file.write_bytes(b'NO_NL_KEY= "nonewline"')
    monkeypatch.delenv("NO_NL_KEY", raising=False)
    load_dotenv(str(env_file))
    assert os.environ.get("NO_NL_KEY") == "nonewline"
