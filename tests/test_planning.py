"""Segment planning and the formatting helpers — pure logic, no ffmpeg."""

from __future__ import annotations

from pathlib import Path

import pytest

from call_recording_splitter import cli

MINUTE = 60.0
OUT = Path("/out")


def plan(duration: float, minutes: float = 15.0, name: str = "rec.m4a"):
    recording = cli.Recording(path=Path("/src") / name, duration=duration)
    return cli.plan_segments(recording, OUT, minutes * MINUTE)


def spans(duration: float, minutes: float = 15.0):
    return [(s.start, s.duration) for s in plan(duration, minutes)]


# --- the examples from the spec -------------------------------------------- #


def test_seventeen_minutes_becomes_15_plus_2():
    assert spans(17 * MINUTE) == [(0.0, 900.0), (900.0, 120.0)]


def test_forty_minutes_becomes_15_15_10():
    assert spans(40 * MINUTE) == [(0.0, 900.0), (900.0, 900.0), (1800.0, 600.0)]


def test_short_file_still_gets_a_single_part():
    assert spans(5 * MINUTE) == [(0.0, 300.0)]


def test_file_of_exactly_one_segment_is_one_part():
    assert spans(15 * MINUTE) == [(0.0, 900.0)]


def test_exact_multiple_produces_no_sliver():
    assert len(plan(30 * MINUTE)) == 2


# --- remainder handling ----------------------------------------------------- #


def test_sub_second_remainder_rides_along_in_the_last_part():
    assert len(plan(30 * MINUTE + 0.02)) == 2


def test_remainder_above_the_floor_becomes_its_own_part():
    assert len(plan(30 * MINUTE + 1.5)) == 3


def test_tail_floor_shrinks_with_the_segment_length():
    assert cli.tail_floor(900.0) == 1.0
    assert cli.tail_floor(3.0) == pytest.approx(0.05)


def test_short_parts_do_not_lose_their_remainder_to_the_floor():
    # 10s of audio in 3s parts: 3 + 3 + 3 + 1, not 3 + 3 + 4.
    assert spans(10.0, minutes=0.05) == [(0.0, 3.0), (3.0, 3.0), (6.0, 3.0), (9.0, 1.0)]


# --- custom part lengths ---------------------------------------------------- #


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(30.0, 2), (7.5, 6), (20.0, 2), (40.0, 1), (60.0, 1)],
)
def test_part_count_follows_the_minutes_argument(minutes, expected):
    assert len(plan(40 * MINUTE, minutes)) == expected


def test_fractional_minutes_split_evenly():
    assert spans(40 * MINUTE, minutes=7.5) == [
        (0.0, 450.0), (450.0, 450.0), (900.0, 450.0),
        (1350.0, 450.0), (1800.0, 450.0), (2250.0, 150.0),
    ]


# --- naming and flags ------------------------------------------------------- #


def test_parts_are_numbered_from_one_in_the_output_folder():
    assert [s.path for s in plan(40 * MINUTE, name="lecture.m4a")] == [
        OUT / "lecture-part-1.m4a",
        OUT / "lecture-part-2.m4a",
        OUT / "lecture-part-3.m4a",
    ]


def test_uppercase_extension_still_yields_lowercase_parts():
    assert plan(60.0, name="CALL.M4A")[0].path == OUT / "CALL-part-1.m4a"


def test_dots_in_the_stem_are_preserved():
    assert plan(60.0, name="2026.01.09 call.m4a")[0].path == OUT / "2026.01.09 call-part-1.m4a"


def test_only_the_final_segment_is_flagged_last():
    assert [s.is_last for s in plan(40 * MINUTE)] == [False, False, True]


def test_segment_numbers_are_sequential():
    assert [s.number for s in plan(40 * MINUTE)] == [1, 2, 3]


# --- formatting ------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0:00:00"),
        (59.4, "0:00:59"),
        (59.6, "0:01:00"),
        (900.0, "0:15:00"),
        (3600.0, "1:00:00"),
        (3661.0, "1:01:01"),
        (None, "—"),
    ],
)
def test_format_duration(seconds, text):
    assert cli.format_duration(seconds) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(3.0, "0:03"), (90.0, "1:30"), (900.0, "15:00"), (3900.0, "1:05:00")],
)
def test_format_span(seconds, text):
    assert cli.format_span(seconds) == text


@pytest.mark.parametrize(
    ("minutes", "text"), [(15.0, "15"), (7.5, "7.5"), (0.05, "0.05"), (1.0, "1")]
)
def test_format_minutes_drops_trailing_zeros(minutes, text):
    assert cli.format_minutes(minutes) == text


@pytest.mark.parametrize(
    ("count", "text"), [(0, "0 parts"), (1, "1 part"), (2, "2 parts")]
)
def test_plural(count, text):
    assert cli.plural(count, "part") == text


# --- ffmpeg stderr tidying -------------------------------------------------- #


def test_clean_error_returns_empty_for_no_output():
    assert cli.clean_error("") == ""
    assert cli.clean_error("\n  \n") == ""


def test_clean_error_keeps_the_most_specific_line():
    assert cli.clean_error("opening input\nreading header\nInvalid data") == "Invalid data"


def test_clean_error_strips_the_leading_path():
    source = Path("/long/path/to/call.m4a")
    assert cli.clean_error(f"{source}: Invalid data found", source) == "Invalid data found"


def test_clean_error_folds_an_embedded_path_to_the_basename():
    source = Path("/long/path/to/call.m4a")
    assert cli.clean_error(f"Error opening {source} for reading", source) == (
        "Error opening call.m4a for reading"
    )


def test_clean_error_truncates_with_an_ellipsis():
    assert cli.clean_error("x" * 500, limit=20) == "x" * 19 + "…"


# --- Recording.status ------------------------------------------------------- #


def test_status_is_failed_when_there_is_an_error():
    rec = cli.Recording(path=Path("a.m4a"), error="boom", written=2)
    assert rec.status == "failed"


def test_status_is_skipped_when_nothing_was_written():
    rec = cli.Recording(path=Path("a.m4a"), skipped=3)
    assert rec.status == "skipped"


def test_status_is_ok_for_a_partial_skip():
    rec = cli.Recording(path=Path("a.m4a"), written=1, skipped=2)
    assert rec.status == "ok"
