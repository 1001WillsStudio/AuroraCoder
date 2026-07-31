"""Unit tests for :mod:`src.training_log`.

QA focus: the trajectory logger writes training data to disk. We verify it
is a *pure side effect* (never raises into the caller), toggles cleanly on
the ``enabled`` flag, writes one JSONL row per call, and that the save-flag
helper degrades safely (default-on) when storage is absent or corrupt.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from src import training_log


@pytest.fixture
def isolated_log_dirs(tmp_path, monkeypatch):
    """Redirect both DATA_DIR and TRAINING_DATA_DIR into a tmp scratch."""
    data_dir = tmp_path / "data"
    train_dir = tmp_path / "train"
    monkeypatch.setattr(training_log, "DATA_DIR", data_dir)
    monkeypatch.setattr(training_log, "TRAINING_DATA_DIR", train_dir)
    return data_dir, train_dir


# --------------------------------------------------------------------------- toggle
def test_record_api_call_disabled_creates_nothing(isolated_log_dirs, tmp_path):
    training_log.record_api_call([{"role": "user", "content": "hi"}],
                                 {"role": "assistant", "content": "yo"},
                                 enabled=False)
    assert not (tmp_path / "train").exists() or not any((tmp_path / "train").iterdir())


def test_record_api_call_enabled_writes_one_jsonl_row(isolated_log_dirs):
    _data, train_dir = isolated_log_dirs
    request = [{"role": "user", "content": "hello"}]
    response = {"role": "assistant", "content": "world"}
    training_log.record_api_call(request, response, enabled=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = train_dir / f"{today}.jsonl"
    assert log_file.is_file()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["request"] == request
    assert row["response"] == response
    # round-trip shape is exactly {request, response} (no timestamp)
    assert set(row.keys()) == {"request", "response"}


def test_record_api_call_never_raises_on_io_failure(tmp_path, monkeypatch):
    """Even if the target dir is unusable, the caller must not see an error."""
    # Point TRAINING_DATA_DIR at a path whose parent is a file -> mkdir raises.
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setattr(training_log, "DATA_DIR", tmp_path)
    monkeypatch.setattr(training_log, "TRAINING_DATA_DIR", blocker / "train")
    # Should be a no-op rather than raising.
    training_log.record_api_call([], {}, enabled=True)


# --------------------------------------------------------------------------- load_save_training_flag
def test_load_save_training_flag_defaults_true_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(training_log, "DATA_DIR", tmp_path)
    assert training_log.load_save_training_flag() is True


def test_load_save_training_flag_reads_false_from_settings(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(
        json.dumps({"other": {"agent": {"save_training_data": False}}})
    )
    monkeypatch.setattr(training_log, "DATA_DIR", tmp_path)
    assert training_log.load_save_training_flag() is False


def test_load_save_training_flag_reads_true_from_settings(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(
        json.dumps({"other": {"agent": {"save_training_data": True}}})
    )
    monkeypatch.setattr(training_log, "DATA_DIR", tmp_path)
    assert training_log.load_save_training_flag() is True


def test_load_save_training_flag_swallows_corrupt_settings(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{ not valid json ")
    monkeypatch.setattr(training_log, "DATA_DIR", tmp_path)
    # Corrupted storage must never raise; degrade to safe default True.
    assert training_log.load_save_training_flag() is True