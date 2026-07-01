"""Tests for channels/apple_notes.py — Apple Notes export via AppleScript."""

import json
import subprocess

import pytest

from alchemia.channels.apple_notes import export_alchemia_notes, export_note_body


@pytest.fixture(autouse=True)
def local_notes_output_dir(monkeypatch, tmp_path):
    import alchemia.channels.apple_notes as mod

    monkeypatch.setattr(mod, "NOTES_OUTPUT_DIR", tmp_path / "notes")


def test_export_notes_success(monkeypatch):
    notes_json = [
        json.dumps({"id": "1", "title": "Note A", "modified": "2026-01-01", "body_length": 100}),
        json.dumps({"id": "2", "title": "Note B", "modified": "2026-01-02", "body_length": 200}),
    ]
    stdout = "\n".join(notes_json) + "\n"

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_alchemia_notes()
    assert len(result) == 2
    assert result[0]["title"] == "Note A"
    assert result[1]["body_length"] == 200


def test_export_notes_no_folder(monkeypatch):
    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ERROR: folder Alchemia not found",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_alchemia_notes()
    assert result == []


def test_export_notes_timeout(monkeypatch):
    def mock_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_alchemia_notes()
    assert result == []


def test_export_notes_nonzero_returncode(monkeypatch):
    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_alchemia_notes()
    assert result == []


def test_export_note_body_success(monkeypatch):
    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="Note body text here", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_note_body("Test Note")
    assert result == "Note body text here"


def test_export_note_body_timeout(monkeypatch):
    def mock_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 15)

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = export_note_body("Test Note")
    assert result == ""
