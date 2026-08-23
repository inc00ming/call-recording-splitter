# Call Recording Splitter

A terminal tool that cuts `.m4a` recordings into consecutive fixed-length
parts — **15 minutes by default**, any length via `--minutes`. It runs
interactively or unattended. Splitting uses `ffmpeg -c copy` (stream copy), so nothing
is re-encoded — the parts are bit-identical slices of the original audio and
the whole run takes seconds, not minutes.

```
lecture.m4a  (40:00)  →  lecture-part-1.m4a  (15:00)
                         lecture-part-2.m4a  (15:00)
                         lecture-part-3.m4a  (10:00)
```

Files already shorter than one part are still copied out as
`name-part-1.m4a`, so the output folder is uniform.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` **and** `ffprobe` on your `PATH`

Or none of the above — [run it in Docker](#docker) instead.

The tool checks for ffmpeg/ffprobe at startup and stops with a red error
message if either is missing.

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
winget install Gyan.FFmpeg   # Windows
```

## Setup

```bash
uv sync
```

That creates the `.venv/` and installs the dependencies from `uv.lock`.

## Run — interactive

```bash
uv run split-recordings
```

With no arguments the tool asks for:

1. the **source folder** holding the `.m4a` files (searched non-recursively,
   extension match is case-insensitive), and
2. the **output folder** for the parts — created for you if it does not exist.

Both prompts re-ask on invalid input. The output folder has to be different
from the source folder, otherwise a second run would treat the parts as inputs.

## Run — non-interactive

Pass both folders and nothing is prompted:

```bash
uv run split-recordings ~/calls ~/calls-split
```

That still stops to ask if parts already exist in the output folder.
`--on-existing` answers that question up front, which is what makes a run
fully unattended:

```bash
uv run split-recordings ~/calls ~/calls-split --on-existing overwrite
uv run split-recordings ~/calls ~/calls-split --on-existing skip
uv run split-recordings ~/calls ~/calls-split --on-existing abort
```

Give just the source and only the output folder is asked for:

```bash
uv run split-recordings ~/calls
```

Bad input is a hard error here rather than a re-prompt: a missing source
folder, a path that is not a folder, or an output folder equal to the source
all print the reason in red and exit `2`. If a prompt is reached with nothing
on stdin — an unattended run that hit the overwrite question — the tool says
so and exits `2` instead of hanging.

### Options

| | |
|---|---|
| `SOURCE` | folder holding the `.m4a` files (prompted for when omitted) |
| `OUTPUT` | folder for the parts, created if missing (prompted for when omitted) |
| `--minutes N` | part length in minutes, fractions allowed (default `15`) |
| `--on-existing {overwrite,skip,abort}` | answer the overwrite question up front |
| `--version` | print the version |
| `-h`, `--help` | full help with examples |

`--minutes` takes any positive number, so `--minutes 30`, `--minutes 7.5` and
`--minutes 0.5` all work; `0`, negatives and non-numbers are rejected with a
usage error.

```bash
uv run split-recordings ~/calls ~/calls-split --minutes 30    # 40:00 -> 30 + 10
uv run split-recordings ~/calls ~/calls-split --minutes 7.5   # 40:00 -> 5x7:30 + 2:30
```

### Other invocation forms

```bash
uv run python -m call_recording_splitter ~/calls ~/calls-split
uv run --no-sync split-recordings         # skip uv's dependency check on repeat runs
```

### In a script

```bash
#!/usr/bin/env bash
set -euo pipefail
uv run --no-sync split-recordings ~/calls ~/calls-split --on-existing skip
```

Exit status is `0` only when every file was split, so `set -e` catches a
partial run.

## Docker

The image carries Python, uv and ffmpeg, so nothing needs installing on the
host. It sets `RUNNING_IN_DOCKER=1`, which makes the CLI skip the prompts and
use the two mount points directly — the banner, progress bars and summary table
all still appear.

| Mount point | Holds |
|---|---|
| `/data/input` | the `.m4a` files to split (read) |
| `/data/output` | the parts (written) |

### Build

```bash
docker build -t call-recording-splitter .
```

### Run — Linux and macOS

```bash
docker run -it --rm \
  --user "$(id -u):$(id -g)" \
  -v /path/to/calls:/data/input \
  -v /path/to/parts:/data/output \
  call-recording-splitter
```

### Run — Windows PowerShell

PowerShell continues lines with a backtick and takes native Windows paths on
the left of the colon:

```powershell
docker run -it --rm `
  -v "C:\Users\me\calls:/data/input" `
  -v "C:\Users\me\parts:/data/output" `
  call-recording-splitter
```

Relative to the current folder, use `${PWD}`:

```powershell
docker run -it --rm -v "${PWD}\calls:/data/input" -v "${PWD}\parts:/data/output" call-recording-splitter
```

`--user` is a Linux concern and is left off above; see the ownership note below.

### `-it` is what keeps the colours

Rich checks whether it is writing to a terminal. `-t` gives the container one,
so you get colour and live progress bars; `-i` lets Ctrl-C through. Without
`-t` the output falls back to plain text with no escape codes — which is what
you want when piping to a log:

```bash
docker run --rm -v ...:/data/input -v ...:/data/output call-recording-splitter > run.log
```

### File ownership

The image runs as a non-root user (uid 1000), so a plain `docker run` never
writes root-owned files. To have the parts owned by *you*, pass your own ids:

```bash
docker run -it --rm --user "$(id -u):$(id -g)" -v ... call-recording-splitter
```

This matters on **Linux**, where a bind mount passes uids through unchanged: a
part written as uid 1000 lands on disk as uid 1000. On **macOS and Windows**,
Docker Desktop remaps everything to the invoking user, so the files come out
yours either way and `--user` changes nothing you can see.

Whichever uid you pick needs write access to the host folder behind
`/data/output`. If it does not have it, every file is reported as
`Permission denied` and the run exits `1` — nothing is silently dropped.

### Passing options

Anything after the image name goes straight to the CLI:

```bash
docker run -it --rm -v ...:/data/input -v ...:/data/output \
  call-recording-splitter --minutes 30 --on-existing skip
docker run --rm call-recording-splitter --help
```

Folders given as arguments override the mount points, so
`... call-recording-splitter /data/input /data/output/today` also works.

### Forgetting a mount

`/data/input` exists in the image but is empty, so a run without `-v` stops
with `No .m4a files found` followed by the exact `docker run` line to use.

## What you see

- a cyan summary of how many files were found and their total duration
- an overall progress bar with percentage, elapsed time and time remaining
- a per-file bar showing the part being written (`lecture.m4a — part 2/3`)
- a green ✓ per finished file, a red ✗ for anything that failed
- a closing table of file / duration / parts / status

Piped or redirected output degrades gracefully — rich drops the animation and
colour, so logs stay readable.

## Behaviour notes

- **Unreadable files are skipped, not fatal.** If `ffprobe` cannot read a file,
  the reason is printed in red and the remaining files still run.
- **Existing output is never clobbered silently.** If any part file already
  exists, you are asked once — `overwrite`, `skip` or `abort` — and that answer
  applies to the whole run.
- **Cut precision.** Stream copy cannot cut mid-frame, so each boundary lands
  on the nearest AAC frame — parts can run up to ~23 ms long and consecutive
  parts overlap by at most that much. Nothing is ever lost; this is the price
  of not re-encoding.
- **Exit codes:** `0` every file split · `1` a file failed or you aborted at
  the overwrite prompt · `2` bad arguments, an unusable folder, or a prompt
  with no input to read · `130` interrupted with Ctrl-C.

## Tests

```bash
uv sync                       # installs pytest from the dev group
uv run pytest                 # the whole suite, ~3 seconds
uv run pytest -v              # per-test names
uv run pytest tests/test_planning.py
uv run pytest -m ffmpeg       # only the tests that shell out to ffmpeg
uv run pytest -k overwrite    # by name
```

| File | Covers |
|---|---|
| `tests/test_planning.py` | segment boundaries, remainders, part naming, duration formatting, ffmpeg error tidying |
| `tests/test_validation.py` | folder checks, `.m4a` discovery, every argument and its rejections |
| `tests/test_ffmpeg_calls.py` | the ffprobe/ffmpeg command contract and failure handling, with `subprocess` faked |
| `tests/test_container.py` | container detection, mount defaults, forgotten mounts, Dockerfile/code agreement |
| `tests/test_cli.py` | full runs through `main()`: both modes, all three overwrite policies, every exit code |

Tests marked `ffmpeg` build real `.m4a` fixtures and split them for real; they
skip automatically when ffmpeg is not installed. They stay fast by splitting a
10-second file with `--minutes 0.05`, so no minute-long fixtures are needed.

## Development

```bash
uv sync                       # install, including dev dependencies
uv run split-recordings       # run
uv run pytest                 # test
uv add <package>              # add a dependency
uv add --dev <package>        # add a dev dependency
```

Layout:

```
pyproject.toml
src/call_recording_splitter/
├── __init__.py
├── __main__.py
└── cli.py          # everything: prompts, ffprobe/ffmpeg calls, rich output
tests/
├── conftest.py     # ffmpeg fixture factory, console capture, scripted prompts
├── test_planning.py
├── test_validation.py
├── test_ffmpeg_calls.py
├── test_container.py
└── test_cli.py
Dockerfile          # multi-stage: uv builds the venv, runtime carries ffmpeg
.dockerignore
```

`tests/` is excluded from the Docker build context, so the image ships only
what it needs to run. `test_container.py` reads the `Dockerfile` as text to
check it has not drifted from the paths and flag the code depends on.
