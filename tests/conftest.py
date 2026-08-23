"""Shared fixtures.

Audio fixtures are real files produced by ffmpeg, but kept to a few seconds —
the part length is a parameter, so ``--minutes 0.05`` exercises multi-part
splitting without minute-long fixtures.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

from call_recording_splitter import cli

HAS_FFMPEG = all(shutil.which(tool) for tool in cli.REQUIRED_TOOLS)
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def duration_of(path: Path) -> float:
    """Read a duration back with ffprobe, for asserting on real output."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


@pytest.fixture
def make_m4a(tmp_path):
    """Factory: ``make_m4a("a.m4a", 10)`` writes 10 seconds of silent AAC."""

    def _make(name: str, seconds: float, folder: Path | None = None) -> Path:
        target = (folder or tmp_path) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", str(seconds), "-c:a", "aac", "-b:a", "32k", str(target),
            ],
            check=True,
        )
        return target

    return _make


@pytest.fixture
def out_dir(tmp_path) -> Path:
    """A path for output that does not exist yet, so creation is exercised."""
    return tmp_path / "parts"


@pytest.fixture
def cap(monkeypatch) -> io.StringIO:
    """Swap the module console for one recording plain, unwrapped text."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        cli,
        "console",
        Console(
            file=buffer,
            width=300,
            force_terminal=False,
            no_color=True,
            highlight=False,
        ),
    )
    return buffer


@pytest.fixture
def answers(monkeypatch):
    """Script the interactive prompts.

    ``asked = answers(["/src", "/out"])`` feeds those to successive
    ``Prompt.ask`` calls and records the prompts in ``asked``. Running out of
    answers raises EOFError, exactly like a closed stdin.
    """

    def _script(values: list[str]) -> list[str]:
        queue = list(values)
        asked: list[str] = []

        def fake_ask(prompt: object = "", **kwargs: object) -> str:
            asked.append(str(prompt))
            if not queue:
                raise EOFError
            return queue.pop(0)

        monkeypatch.setattr(cli.Prompt, "ask", staticmethod(fake_ask))
        return asked

    return _script
