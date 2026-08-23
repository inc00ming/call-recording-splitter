"""The ffprobe/ffmpeg contract, with subprocess faked.

These lock down the flags that make the split lossless and the behaviour when
a tool exits non-zero — cases that are awkward to provoke with real binaries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from call_recording_splitter import cli

SOURCE = Path("/src/rec.m4a")


def fake_run(monkeypatch, *, returncode=0, stdout="", stderr="", side_effect=None):
    """Replace subprocess.run and hand back the captured call."""
    captured: dict[str, object] = {}

    def _run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        if side_effect is not None:
            side_effect(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(cli.subprocess, "run", _run)
    return captured


def segment(number=1, start=0.0, duration=900.0, is_last=False, path=Path("/out/a-part-1.m4a")):
    return cli.Segment(number=number, start=start, duration=duration, is_last=is_last, path=path)


# --- write_segment: the stream-copy contract -------------------------------- #


def test_the_audio_is_copied_not_re_encoded(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment())
    command = captured["command"]
    assert command[command.index("-c") + 1] == "copy"


def test_seeking_happens_before_the_input_so_it_is_fast(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment(start=900.0))
    command = captured["command"]
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "900.000"


def test_only_audio_streams_are_mapped(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment())
    assert captured["command"][captured["command"].index("-map") + 1] == "0:a"


def test_a_middle_segment_is_bounded_by_its_length(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment(duration=900.0, is_last=False))
    assert captured["command"][captured["command"].index("-t") + 1] == "900.000"


def test_the_last_segment_runs_to_the_end_of_the_file(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment(duration=120.0, is_last=True))
    assert "-t" not in captured["command"]


def test_the_part_path_is_the_output_argument(monkeypatch):
    captured = fake_run(monkeypatch)
    cli.write_segment(SOURCE, segment(path=Path("/out/a-part-3.m4a")))
    assert captured["command"][-1] == "/out/a-part-3.m4a"


def test_a_clean_run_reports_no_reason(monkeypatch):
    fake_run(monkeypatch)
    assert cli.write_segment(SOURCE, segment()) is None


# --- write_segment: failure ------------------------------------------------- #


def test_a_failed_write_reports_the_reason(monkeypatch):
    fake_run(monkeypatch, returncode=1, stderr="opening\nOutput file is empty")
    assert cli.write_segment(SOURCE, segment()) == "Output file is empty"


def test_a_failed_write_deletes_the_truncated_part(tmp_path, monkeypatch):
    target = tmp_path / "a-part-1.m4a"
    fake_run(
        monkeypatch,
        returncode=1,
        stderr="Invalid argument",
        side_effect=lambda _cmd: target.write_bytes(b"half a file"),
    )
    assert cli.write_segment(SOURCE, segment(path=target)) == "Invalid argument"
    assert not target.exists()


def test_a_silent_failure_still_reports_the_exit_code(monkeypatch):
    fake_run(monkeypatch, returncode=137, stderr="")
    assert cli.write_segment(SOURCE, segment()) == "ffmpeg exited with code 137"


def test_a_missing_binary_is_reported_rather_than_raised(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("No such file or directory: 'ffmpeg'")

    monkeypatch.setattr(cli.subprocess, "run", boom)
    assert "No such file" in (cli.write_segment(SOURCE, segment()) or "")


# --- probe_duration --------------------------------------------------------- #


def test_a_duration_is_parsed(monkeypatch):
    fake_run(monkeypatch, stdout="1020.000000\n")
    assert cli.probe_duration(SOURCE) == (1020.0, None)


def test_probe_asks_only_for_the_format_duration(monkeypatch):
    captured = fake_run(monkeypatch, stdout="1.0")
    cli.probe_duration(SOURCE)
    assert "format=duration" in captured["command"]


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "fragment"),
    [
        ("", "Invalid data found", 1, "Invalid data found"),
        ("", "", 1, "ffprobe exited with code 1"),
        ("N/A\n", "", 0, "no readable duration"),
        ("", "", 0, "no readable duration"),
        ("0.000000\n", "", 0, "zero"),
    ],
)
def test_unreadable_files_give_a_reason_not_an_exception(
    monkeypatch, stdout, stderr, returncode, fragment
):
    fake_run(monkeypatch, stdout=stdout, stderr=stderr, returncode=returncode)
    duration, reason = cli.probe_duration(SOURCE)
    assert duration is None
    assert fragment in (reason or "")


def test_the_probe_is_given_a_timeout_so_it_cannot_hang(monkeypatch):
    captured = fake_run(monkeypatch, stdout="1.0")
    cli.probe_duration(SOURCE)
    assert captured["kwargs"]["timeout"] == cli.PROBE_TIMEOUT


def test_a_hung_probe_times_out_instead_of_blocking(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=cli.PROBE_TIMEOUT)

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    duration, reason = cli.probe_duration(SOURCE)
    assert duration is None and "timed out" in (reason or "")
