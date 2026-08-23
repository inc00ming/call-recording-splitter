"""Interactive CLI that splits .m4a recordings into fixed-length parts.

Splitting is done with ``ffmpeg -c copy`` (stream copy), so parts are written
losslessly and roughly as fast as the disk can move the bytes.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NoReturn

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as esc
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

DEFAULT_MINUTES = 15.0
#: Upper bound on the remainder that gets folded into the last part rather than
#: becoming a sliver of its own (a 30:00.02 file is 2 parts, not 3). Scaled down
#: for short parts, where a whole second would be a meaningful slice.
MIN_TAIL_SECONDS = 1.0
REQUIRED_TOOLS = ("ffmpeg", "ffprobe")
PROBE_TIMEOUT = 60
POLICIES = ("overwrite", "skip", "abort")

#: Set by the Dockerfile. Its presence swaps the prompts for the mount points
#: below, so the image needs no arguments to do the obvious thing.
CONTAINER_FLAG = "RUNNING_IN_DOCKER"
CONTAINER_INPUT = "/data/input"
CONTAINER_OUTPUT = "/data/output"
MOUNT_HINT = (
    "Mount the folders when you start the container, for example:\n"
    "  docker run -it --rm --user \"$(id -u):$(id -g)\" "
    "-v /path/to/calls:/data/input -v /path/to/parts:/data/output "
    "call-recording-splitter"
)

try:
    __version__ = version("call-recording-splitter")
except PackageNotFoundError:  # running straight from a source checkout
    __version__ = "0.0.0+dev"

console = Console()


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Segment:
    """One output part of a recording."""

    number: int
    start: float
    duration: float
    is_last: bool
    path: Path


@dataclass
class Recording:
    """A source .m4a file and everything we learn about it along the way."""

    path: Path
    duration: float | None = None
    error: str | None = None
    segments: list[Segment] = field(default_factory=list)
    written: int = 0
    skipped: int = 0

    @property
    def status(self) -> str:
        if self.error is not None:
            return "failed"
        if self.written == 0 and self.skipped > 0:
            return "skipped"
        return "ok"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def format_duration(seconds: float | None) -> str:
    """Render seconds as ``H:MM:SS``."""
    if seconds is None:
        return "—"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_span(seconds: float) -> str:
    """Compact part length: ``15:00``, ``1:05:00``, ``0:03``."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_minutes(minutes: float) -> str:
    """``15`` / ``7.5`` — no trailing zeros on whole values."""
    return f"{minutes:g}"


def clean_error(text: str, source: Path | None = None, limit: int = 160) -> str:
    """Pull the most specific line out of an ffmpeg/ffprobe stderr dump.

    ffmpeg prefixes its messages with the full input path, which is noise next
    to a filename we already print, so it is folded down to the basename.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    message = lines[-1]
    if source is not None:
        message = message.replace(str(source), source.name)
        message = message.removeprefix(f"{source.name}: ")
    return message if len(message) <= limit else message[: limit - 1] + "…"


def plural(count: int, noun: str) -> str:
    """``1 part`` / ``3 parts``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class Failure(Exception):
    """Ends the run with an exit code; the reason has already been printed."""

    def __init__(self, code: int = 2) -> None:
        super().__init__(f"exit {code}")
        self.code = code


def in_container() -> bool:
    """True when the image's RUNNING_IN_DOCKER is set to anything meaningful."""
    return os.environ.get(CONTAINER_FLAG, "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
    }


def abort(message: str, code: int = 2) -> NoReturn:
    """Stop the run with a red message. Used where a prompt would have looped."""
    console.print(f"[red]{message}[/red]")
    raise Failure(code)


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #


def show_banner(minutes: float) -> None:
    console.print()
    console.print(
        Panel(
            Align.center(
                Text.from_markup(
                    "[bold cyan]Call Recording Splitter[/bold cyan]\n"
                    f"[dim]Cuts .m4a recordings into "
                    f"{format_minutes(minutes)}-minute parts — "
                    "lossless stream copy, no re-encoding[/dim]"
                )
            ),
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()


def require_ffmpeg() -> None:
    """Abort with a clear red error if ffmpeg/ffprobe are not on PATH."""
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if not missing:
        return
    console.print(
        Panel(
            Text.from_markup(
                f"[bold red]Not found on your PATH:[/bold red] {', '.join(missing)}\n\n"
                "This tool shells out to ffmpeg (which ships with ffprobe).\n"
                "Install it, then run the tool again:\n\n"
                "  [bold]macOS[/bold]    brew install ffmpeg\n"
                "  [bold]Debian[/bold]   sudo apt install ffmpeg\n"
                "  [bold]Windows[/bold]  winget install Gyan.FFmpeg"
            ),
            title="[bold red]Missing dependency[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )
    raise Failure(1)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #


def check_source(path: Path) -> str | None:
    """Validation shared by the prompt loop and the command-line argument."""
    if not path.exists():
        return f"No such folder: {esc(str(path))}"
    if not path.is_dir():
        return f"Not a folder: {esc(str(path))}"
    return None


def prepare_output(path: Path, source: Path) -> tuple[Path | None, str | None]:
    """Validate the output folder and create it. Returns ``(resolved, problem)``."""
    if path.exists() and not path.is_dir():
        return None, f"Not a folder: {esc(str(path))}"
    created = not path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        return None, f"Cannot create {esc(str(path))}: {esc(reason)}"
    resolved = path.resolve()
    if resolved == source:
        return None, (
            "The output folder must differ from the source folder, "
            "otherwise the parts would be picked up as inputs next run."
        )
    if created:
        console.print(f"[dim]Created {esc(str(resolved))}[/dim]")
    return resolved, None


def resolve_source(argument: str | None, hint: str = "") -> Path:
    """Take the source folder from the command line, or ask for it."""
    if argument is not None:
        path = Path(argument).expanduser()
        problem = check_source(path)
        if problem is not None:
            abort(f"{problem}\n{hint}" if hint else problem)
        return path.resolve()

    while True:
        raw = Prompt.ask("[bold]Source folder[/bold] containing the .m4a files").strip()
        if not raw:
            console.print("[red]Please enter a path.[/red]")
            continue
        path = Path(raw).expanduser()
        problem = check_source(path)
        if problem is not None:
            console.print(f"[red]{problem}[/red]")
            continue
        return path.resolve()


def resolve_output(argument: str | None, source: Path, hint: str = "") -> Path:
    """Take the output folder from the command line, or ask for it."""
    if argument is not None:
        resolved, problem = prepare_output(Path(argument).expanduser(), source)
        if problem is not None or resolved is None:
            problem = problem or "Unusable output folder."
            abort(f"{problem}\n{hint}" if hint else problem)
        return resolved

    while True:
        raw = Prompt.ask("[bold]Output folder[/bold] for the split parts").strip()
        if not raw:
            console.print("[red]Please enter a path.[/red]")
            continue
        resolved, problem = prepare_output(Path(raw).expanduser(), source)
        if problem is not None or resolved is None:
            console.print(f"[red]{problem}[/red]")
            continue
        return resolved


def report_existing(existing: list[Path]) -> None:
    sample = ", ".join(esc(p.name) for p in existing[:3])
    if len(existing) > 3:
        sample += f", … (+{len(existing) - 3} more)"
    verb = "exists" if len(existing) == 1 else "exist"
    console.print()
    console.print(
        f"[yellow]![/yellow] [yellow]{plural(len(existing), 'output file')} already "
        f"{verb}:[/yellow] [bold]{sample}[/bold]"
    )


def ask_overwrite_policy() -> str:
    """Ask once, up front, what to do about pre-existing output files."""
    return Prompt.ask(
        "[yellow]Overwrite them, skip them, or abort?[/yellow]",
        choices=list(POLICIES),
        default="skip",
    )


# --------------------------------------------------------------------------- #
# ffprobe / ffmpeg
# --------------------------------------------------------------------------- #


def find_m4a_files(folder: Path) -> list[Path]:
    """Non-recursive, case-insensitive .m4a lookup."""
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".m4a"),
        key=lambda p: p.name.lower(),
    )


def probe_duration(path: Path) -> tuple[float | None, str | None]:
    """Return ``(duration_seconds, None)`` or ``(None, reason)``."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return None, f"ffprobe timed out after {PROBE_TIMEOUT}s"
    except OSError as exc:
        return None, str(exc)

    if proc.returncode != 0:
        return None, clean_error(proc.stderr, path) or f"ffprobe exited with code {proc.returncode}"
    raw = proc.stdout.strip()
    try:
        duration = float(raw)
    except ValueError:
        return None, f"ffprobe reported no readable duration ({raw!r})"
    if duration <= 0:
        return None, "duration reported as zero"
    return duration, None


def write_segment(source: Path, segment: Segment) -> str | None:
    """Stream-copy one part. Returns None on success, else the failure reason."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{segment.start:.3f}",
        "-i",
        str(source),
    ]
    if not segment.is_last:
        command += ["-t", f"{segment.duration:.3f}"]
    command += ["-map", "0:a", "-c", "copy", str(segment.path)]

    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        return str(exc)
    if proc.returncode != 0:
        # ffmpeg may have left a truncated file behind; don't keep corrupt output.
        segment.path.unlink(missing_ok=True)
        return clean_error(proc.stderr, source) or f"ffmpeg exited with code {proc.returncode}"
    return None


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def tail_floor(segment_seconds: float) -> float:
    """Remainders below this ride along in the last part instead of becoming one."""
    return min(MIN_TAIL_SECONDS, segment_seconds / 60)


def plan_segments(
    recording: Recording, output_dir: Path, segment_seconds: float
) -> list[Segment]:
    """Consecutive slices of ``segment_seconds``; the last one holds the remainder.

    Files at or under one segment still get a single ``-part-1`` output so the
    output folder is uniform.
    """
    duration = recording.duration
    assert duration is not None

    bounds: list[tuple[float, float]] = []
    if duration <= segment_seconds:
        bounds.append((0.0, duration))
    else:
        floor = tail_floor(segment_seconds)
        start = 0.0
        while start < duration - floor:
            bounds.append((start, min(segment_seconds, duration - start)))
            start += segment_seconds

    stem = recording.path.stem
    return [
        Segment(
            number=i + 1,
            start=start,
            duration=length,
            is_last=(i == len(bounds) - 1),
            path=output_dir / f"{stem}-part-{i + 1}.m4a",
        )
        for i, (start, length) in enumerate(bounds)
    ]


def probe_all(files: list[Path]) -> list[Recording]:
    recordings = [Recording(path=path) for path in files]
    with console.status("[cyan]Reading durations with ffprobe…[/cyan]", spinner="dots"):
        for recording in recordings:
            duration, error = probe_duration(recording.path)
            recording.duration = duration
            recording.error = error
    return recordings


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #


def build_progress() -> tuple[Progress, Progress, Panel]:
    overall = Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None, complete_style="blue", finished_style="green"),
        TaskProgressColumn(),
        TextColumn("[dim]•[/dim]"),
        TimeElapsedColumn(),
        TextColumn("[dim]elapsed •[/dim]"),
        TimeRemainingColumn(),
        TextColumn("[dim]left[/dim]"),
        console=console,
    )
    per_file = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("{task.description}"),
        BarColumn(bar_width=30, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(),
        TextColumn("[dim]parts[/dim]"),
        console=console,
    )
    panel = Panel(
        Group(overall, per_file),
        title="[bold]Splitting[/bold]",
        border_style="blue",
        padding=(1, 2),
    )
    return overall, per_file, panel


def split_all(recordings: list[Recording], policy: str) -> None:
    """Write every part, driving both progress bars and the per-file log lines."""
    total_audio = sum(r.duration or 0.0 for r in recordings)
    overall, per_file, panel = build_progress()
    overall_task = overall.add_task("Overall", total=max(total_audio, 1e-9))

    with Live(panel, console=console, refresh_per_second=12):
        for position, recording in enumerate(recordings, start=1):
            name = esc(recording.path.name)
            count = len(recording.segments)
            overall.update(
                overall_task, description=f"Overall  ·  file {position}/{len(recordings)}"
            )
            file_task = per_file.add_task(
                f"[bold]{name}[/bold] — part 1/{count}", total=count
            )

            handled = 0
            for segment in recording.segments:
                per_file.update(
                    file_task,
                    description=f"[bold]{name}[/bold] — part {segment.number}/{count}",
                )
                if policy == "skip" and segment.path.exists():
                    recording.skipped += 1
                else:
                    failure = write_segment(recording.path, segment)
                    if failure is not None:
                        recording.error = failure
                        break
                    recording.written += 1
                handled += 1
                per_file.advance(file_task)
                overall.advance(overall_task, segment.duration)

            if recording.error is not None:
                # Keep the overall bar honest about the audio we never touched.
                unprocessed = sum(s.duration for s in recording.segments[handled:])
                overall.advance(overall_task, unprocessed)

            per_file.remove_task(file_task)
            report_recording(recording)

        overall.update(overall_task, description="Overall  ·  done", completed=total_audio)


def report_recording(recording: Recording) -> None:
    """One green/red/yellow line per finished file, printed above the bars."""
    name = esc(recording.path.name)
    duration = format_duration(recording.duration)
    if recording.error is not None:
        console.print(
            f"  [red]✗[/red] [bold]{name}[/bold] "
            f"[yellow]{duration}[/yellow]  [red]{esc(recording.error)}[/red]"
        )
    elif recording.status == "skipped":
        console.print(
            f"[yellow]  •[/yellow] [bold]{name}[/bold] [yellow]{duration}[/yellow]  "
            f"[yellow]skipped — {plural(recording.skipped, 'part')} already there[/yellow]"
        )
    else:
        extra = f", {recording.skipped} skipped" if recording.skipped else ""
        console.print(
            f"[green]  ✓[/green] [bold]{name}[/bold] [yellow]{duration}[/yellow]  "
            f"[dim]→[/dim] [green]{plural(recording.written, 'part')}{extra}[/green]"
        )


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #


def show_summary(recordings: list[Recording], output_dir: Path) -> None:
    table = Table(
        title="[bold]Summary[/bold]",
        box=box.SIMPLE_HEAVY,
        header_style="bold magenta",
        title_justify="left",
        pad_edge=False,
    )
    table.add_column("File", style="bold", overflow="fold")
    table.add_column("Duration", justify="right", style="yellow")
    table.add_column("Parts", justify="right")
    table.add_column("Status", justify="center")

    status_cell = {
        "ok": "[green]OK[/green]",
        "skipped": "[yellow]SKIPPED[/yellow]",
        "failed": "[red]FAILED[/red]",
    }
    for recording in recordings:
        table.add_row(
            esc(recording.path.name),
            format_duration(recording.duration),
            str(recording.written),
            status_cell[recording.status],
        )

    console.print()
    console.print(table)

    written = sum(r.written for r in recordings)
    failed = [r for r in recordings if r.status == "failed"]
    tail = (
        f"\n[red]{plural(len(failed), 'file')} failed — see the reasons above.[/red]"
        if failed
        else ""
    )
    console.print(
        Panel(
            Text.from_markup(
                f"[bold green]Done[/bold green] — [bold]{plural(written, 'part')}[/bold] "
                f"written to [bold green]{esc(str(output_dir))}[/bold green]{tail}"
            ),
            border_style="green",
            padding=(1, 2),
        )
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


DESCRIPTION = """\
Split .m4a recordings into consecutive fixed-length parts (15 minutes by
default) using an ffmpeg stream copy, so the parts are lossless and written as
fast as the disk allows.

Folders left off the command line are asked for interactively; pass both to
run unattended.
"""

EXAMPLES = """\
examples:
  # ask for both folders
  split-recordings

  # unattended, unless parts are already in the output folder
  split-recordings ~/calls ~/calls-split

  # unattended whatever the output folder already holds
  split-recordings ~/calls ~/calls-split --on-existing overwrite
  split-recordings ~/calls ~/calls-split --on-existing skip

  # a different part length
  split-recordings ~/calls ~/calls-split --minutes 30
  split-recordings ~/calls ~/calls-split --minutes 7.5

exit codes:
  0    every file split
  1    a file failed, or you aborted at the overwrite prompt
  2    bad arguments, an unusable folder, or a prompt with no input to read
  130  interrupted with Ctrl-C
"""


def positive_minutes(raw: str) -> float:
    """argparse type for --minutes: any finite number above zero."""
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {raw!r}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="split-recordings",
        description=DESCRIPTION,
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "source",
        nargs="?",
        metavar="SOURCE",
        help="folder holding the .m4a files (prompted for when omitted)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        metavar="OUTPUT",
        help="folder to write the parts into, created if missing (prompted for when omitted)",
    )
    parser.add_argument(
        "--minutes",
        type=positive_minutes,
        default=DEFAULT_MINUTES,
        metavar="N",
        help=(
            f"length of each part in minutes, fractions allowed "
            f"(default: {format_minutes(DEFAULT_MINUTES)})"
        ),
    )
    parser.add_argument(
        "--on-existing",
        choices=POLICIES,
        metavar="{overwrite,skip,abort}",
        help=(
            "answer the overwrite question up front instead of being asked; "
            "required for an unattended run whose output folder is not empty"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    segment_seconds = args.minutes * 60
    show_banner(args.minutes)
    require_ffmpeg()

    # In a container the mount points stand in for the prompts, so an image with
    # no arguments still runs start to finish. Explicit arguments still win.
    containerised = in_container()
    hint = MOUNT_HINT if containerised else ""
    source_arg = args.source
    output_arg = args.output
    if containerised:
        source_arg = source_arg or CONTAINER_INPUT
        output_arg = output_arg or CONTAINER_OUTPUT
        console.print(
            f"[cyan]Container mode — reading [bold]{esc(source_arg)}[/bold], "
            f"writing [bold]{esc(output_arg)}[/bold][/cyan]"
        )

    source = resolve_source(source_arg, hint)
    output_dir = resolve_output(output_arg, source, hint)
    console.print()

    files = find_m4a_files(source)
    if not files:
        missing = f"No .m4a files found in {esc(str(source))}."
        console.print(f"[red]{missing}{chr(10) + hint if hint else ''}[/red]")
        return 1

    recordings = probe_all(files)
    unreadable = [r for r in recordings if r.error is not None]
    for recording in unreadable:
        console.print(
            f"[red]✗ Skipping {esc(recording.path.name)}: {esc(recording.error or '')}[/red]"
        )

    readable = [r for r in recordings if r.error is None]
    total = sum(r.duration or 0.0 for r in readable)
    note = f" [red]({len(unreadable)} unreadable, skipped)[/red]" if unreadable else ""
    console.print(
        f"[cyan]Found {plural(len(files), '.m4a file')} · total duration "
        f"{format_duration(total)}[/cyan]{note}"
    )

    if not readable:
        console.print("[red]Nothing left to split.[/red]")
        return 1

    for recording in readable:
        recording.segments = plan_segments(recording, output_dir, segment_seconds)

    planned = sum(len(r.segments) for r in readable)
    console.print(
        f"[cyan]Writing {plural(planned, 'part')} of up to "
        f"{format_span(segment_seconds)} each.[/cyan]"
    )

    existing = [s.path for r in readable for s in r.segments if s.path.exists()]
    policy = "overwrite"
    if existing:
        report_existing(existing)
        if args.on_existing is not None:
            policy = args.on_existing
            console.print(f"[yellow]→ {policy} (--on-existing)[/yellow]")
        else:
            policy = ask_overwrite_policy()
        if policy == "abort":
            console.print("[red]Aborted — nothing was written.[/red]")
            return 1

    console.print()
    split_all(readable, policy)
    show_summary(recordings, output_dir)
    return 1 if any(r.status == "failed" for r in recordings) else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except Failure as failure:
        return failure.code
    except KeyboardInterrupt:
        console.print("\n[red]Aborted.[/red]")
        return 130
    except EOFError:
        # A prompt was reached with nothing on stdin — an unattended run that
        # was not given everything it needs up front.
        console.print(
            "\n[red]A prompt had no input to read. Supply the folders (and "
            "--on-existing) on the command line:[/red]\n"
            "[red]  split-recordings <source> <output> "
            "--on-existing overwrite|skip|abort[/red]"
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
