"""Folder checks, file discovery and argument parsing."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from call_recording_splitter import cli

# --- find_m4a_files --------------------------------------------------------- #


@pytest.fixture
def library(tmp_path):
    for name in ("b.m4a", "A.M4A", "c.M4a", "notes.txt", "song.mp3", ".m4a"):
        (tmp_path / name).touch()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.m4a").touch()
    (tmp_path / "folder.m4a").mkdir()
    return tmp_path


def test_extension_match_is_case_insensitive(library):
    assert [p.name for p in cli.find_m4a_files(library)] == ["A.M4A", "b.m4a", "c.M4a"]


def test_search_does_not_recurse(library):
    assert "deep.m4a" not in {p.name for p in cli.find_m4a_files(library)}


def test_other_extensions_are_ignored(library):
    found = {p.name for p in cli.find_m4a_files(library)}
    assert "notes.txt" not in found and "song.mp3" not in found


def test_a_directory_named_like_a_recording_is_ignored(library):
    assert "folder.m4a" not in {p.name for p in cli.find_m4a_files(library)}


def test_empty_folder_yields_nothing(tmp_path):
    assert cli.find_m4a_files(tmp_path) == []


# --- check_source ----------------------------------------------------------- #


def test_missing_source_is_reported(tmp_path):
    assert "No such folder" in (cli.check_source(tmp_path / "nope") or "")


def test_source_that_is_a_file_is_reported(tmp_path):
    target = tmp_path / "a.m4a"
    target.touch()
    assert "Not a folder" in (cli.check_source(target) or "")


def test_a_real_folder_passes(tmp_path):
    assert cli.check_source(tmp_path) is None


# --- prepare_output --------------------------------------------------------- #


def test_output_folder_is_created_including_parents(tmp_path, cap):
    target = tmp_path / "a" / "b" / "parts"
    resolved, problem = cli.prepare_output(target, tmp_path)
    assert problem is None
    assert resolved == target.resolve() and target.is_dir()


def test_creating_the_output_folder_is_announced(tmp_path, cap):
    cli.prepare_output(tmp_path / "parts", tmp_path)
    assert "Created" in cap.getvalue()


def test_an_existing_output_folder_is_reused_quietly(tmp_path, cap):
    existing = tmp_path / "parts"
    existing.mkdir()
    resolved, problem = cli.prepare_output(existing, tmp_path)
    assert problem is None and resolved == existing.resolve()
    assert "Created" not in cap.getvalue()


def test_output_pointing_at_a_file_is_rejected(tmp_path, cap):
    target = tmp_path / "a.m4a"
    target.touch()
    resolved, problem = cli.prepare_output(target, tmp_path)
    assert resolved is None and "Not a folder" in (problem or "")


def test_output_equal_to_source_is_rejected(tmp_path, cap):
    source = tmp_path / "calls"
    source.mkdir()
    resolved, problem = cli.prepare_output(source, source.resolve())
    assert resolved is None and "must differ from the source" in (problem or "")


def test_output_equal_to_source_via_a_relative_path_is_rejected(tmp_path, cap, monkeypatch):
    source = tmp_path / "calls"
    source.mkdir()
    monkeypatch.chdir(source)
    resolved, problem = cli.prepare_output(Path("."), source.resolve())
    assert resolved is None and "must differ from the source" in (problem or "")


# --- positive_minutes ------------------------------------------------------- #


@pytest.mark.parametrize(("raw", "value"), [("15", 15.0), ("7.5", 7.5), ("0.05", 0.05)])
def test_positive_minutes_accepts_positive_numbers(raw, value):
    assert cli.positive_minutes(raw) == pytest.approx(value)


@pytest.mark.parametrize("raw", ["0", "-1", "-0.5", "abc", "", "nan", "inf", "-inf"])
def test_positive_minutes_rejects_everything_else(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.positive_minutes(raw)


# --- parse_args ------------------------------------------------------------- #


def test_defaults_are_interactive_and_fifteen_minutes():
    args = cli.parse_args([])
    assert args.source is None
    assert args.output is None
    assert args.on_existing is None
    assert args.minutes == cli.DEFAULT_MINUTES == 15.0


def test_both_folders_are_positional():
    args = cli.parse_args(["/in", "/out"])
    assert (args.source, args.output) == ("/in", "/out")


def test_source_alone_leaves_the_output_to_be_prompted():
    args = cli.parse_args(["/in"])
    assert args.source == "/in" and args.output is None


@pytest.mark.parametrize("policy", ["overwrite", "skip", "abort"])
def test_on_existing_accepts_each_policy(policy):
    assert cli.parse_args(["/in", "/out", "--on-existing", policy]).on_existing == policy


def test_on_existing_rejects_an_unknown_policy():
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["/in", "/out", "--on-existing", "maybe"])
    assert exc.value.code == 2


def test_minutes_is_parsed_as_a_number():
    assert cli.parse_args(["--minutes", "7.5"]).minutes == 7.5


@pytest.mark.parametrize("raw", ["0", "-3", "abc", "nan"])
def test_minutes_rejects_bad_values(raw):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--minutes", raw])
    assert exc.value.code == 2


def test_a_third_positional_is_rejected():
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["/in", "/out", "/extra"])
    assert exc.value.code == 2


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--help"])
    assert exc.value.code == 0
    assert "--minutes" in capsys.readouterr().out
