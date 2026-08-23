"""End-to-end runs through main(): real ffmpeg, both modes, every exit code."""

from __future__ import annotations

import os

import pytest

from call_recording_splitter import cli

from .conftest import duration_of, needs_ffmpeg

pytestmark = needs_ffmpeg

# Three-second parts keep the fixtures small while still splitting many ways.
THREE_SECOND_PARTS = ["--minutes", "0.05"]


def parts(folder, stem="rec"):
    return sorted(folder.glob(f"{stem}-part-*.m4a"), key=lambda p: p.name)


# --- the happy path --------------------------------------------------------- #


@pytest.mark.ffmpeg
def test_unattended_run_splits_and_reports_success(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 10, folder=tmp_path / "src")
    code = cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    assert code == 0
    assert [p.name for p in parts(out_dir)] == [
        "rec-part-1.m4a", "rec-part-2.m4a", "rec-part-3.m4a", "rec-part-4.m4a",
    ]


@pytest.mark.ffmpeg
def test_the_last_part_holds_the_remainder(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 10, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    lengths = [duration_of(p) for p in parts(out_dir)]
    assert lengths[:3] == pytest.approx([3.0, 3.0, 3.0], abs=0.1)
    assert lengths[3] == pytest.approx(1.0, abs=0.1)


@pytest.mark.ffmpeg
def test_nothing_is_re_encoded(make_m4a, tmp_path, out_dir, cap):
    source = make_m4a("rec.m4a", 4, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    # A stream copy leaves the audio bytes alone, so part 1 is a prefix slice.
    assert parts(out_dir)[0].stat().st_size < source.stat().st_size


@pytest.mark.ffmpeg
def test_a_file_shorter_than_one_part_is_still_copied_out(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 2, folder=tmp_path / "src")
    assert cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS]) == 0
    assert [p.name for p in parts(out_dir)] == ["rec-part-1.m4a"]


@pytest.mark.ffmpeg
def test_uppercase_extension_is_found_and_lower_cased(make_m4a, tmp_path, out_dir, cap):
    make_m4a("CALL.M4A", 2, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    assert [p.name for p in out_dir.iterdir()] == ["CALL-part-1.m4a"]


@pytest.mark.ffmpeg
def test_the_output_folder_is_created(make_m4a, tmp_path, cap):
    make_m4a("rec.m4a", 2, folder=tmp_path / "src")
    target = tmp_path / "deep" / "parts"
    cli.main([str(tmp_path / "src"), str(target), *THREE_SECOND_PARTS])
    assert target.is_dir()


@pytest.mark.ffmpeg
def test_minutes_changes_the_part_count(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 10, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), "--minutes", "0.1"])  # 6s parts
    assert len(parts(out_dir)) == 2


@pytest.mark.ffmpeg
def test_the_default_part_length_is_fifteen_minutes(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 5, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir)])
    assert len(parts(out_dir)) == 1
    assert "15:00" in cap.getvalue()


# --- what the run prints ---------------------------------------------------- #


@pytest.mark.ffmpeg
def test_the_summary_reports_the_count_and_total_duration(make_m4a, tmp_path, out_dir, cap):
    make_m4a("a.m4a", 4, folder=tmp_path / "src")
    make_m4a("b.m4a", 6, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    text = cap.getvalue()
    assert "Found 2 .m4a files" in text
    assert "0:00:10" in text


@pytest.mark.ffmpeg
def test_finished_files_are_ticked_and_tabulated(make_m4a, tmp_path, out_dir, cap):
    make_m4a("rec.m4a", 4, folder=tmp_path / "src")
    cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS])
    text = cap.getvalue()
    assert "✓ rec.m4a" in text
    assert "Summary" in text and "Duration" in text and "Parts" in text and "Status" in text
    assert "OK" in text
    assert str(out_dir) in text


# --- failures that must not stop the run ------------------------------------ #


@pytest.mark.ffmpeg
def test_an_unreadable_file_is_skipped_and_the_rest_still_run(make_m4a, tmp_path, out_dir, cap):
    source = tmp_path / "src"
    make_m4a("good.m4a", 4, folder=source)
    (source / "broken.m4a").write_bytes(os.urandom(2048))
    code = cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS])
    assert code == 1
    assert len(parts(out_dir, "good")) == 2
    text = cap.getvalue()
    assert "Skipping broken.m4a" in text and "FAILED" in text
    assert "1 unreadable" in text


@pytest.mark.ffmpeg
def test_the_failure_reason_is_shown_without_the_full_path(make_m4a, tmp_path, out_dir, cap):
    source = tmp_path / "src"
    (source).mkdir()
    (source / "broken.m4a").write_bytes(os.urandom(2048))
    cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS])
    assert "Invalid data found" in cap.getvalue()
    assert str(source / "broken.m4a") not in cap.getvalue()


@pytest.mark.ffmpeg
def test_a_folder_of_only_unreadable_files_stops_cleanly(tmp_path, out_dir, cap):
    source = tmp_path / "src"
    source.mkdir()
    (source / "broken.m4a").write_bytes(os.urandom(2048))
    assert cli.main([str(source), str(out_dir)]) == 1
    assert "Nothing left to split" in cap.getvalue()


@pytest.mark.ffmpeg
def test_no_recordings_at_all_is_an_error(tmp_path, out_dir, cap):
    source = tmp_path / "src"
    source.mkdir()
    (source / "notes.txt").write_text("hello")
    assert cli.main([str(source), str(out_dir)]) == 1
    assert "No .m4a files found" in cap.getvalue()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
@pytest.mark.ffmpeg
def test_an_unwritable_output_folder_fails_every_file_without_crashing(
    make_m4a, tmp_path, cap
):
    make_m4a("a.m4a", 4, folder=tmp_path / "src")
    make_m4a("b.m4a", 4, folder=tmp_path / "src")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        assert cli.main([str(tmp_path / "src"), str(locked), *THREE_SECOND_PARTS]) == 1
        text = cap.getvalue()
        assert text.count("✗") >= 2 and "Permission denied" in text
        assert list(locked.iterdir()) == []  # no truncated parts left behind
    finally:
        locked.chmod(0o755)


# --- arguments that should stop the run before any work --------------------- #


def test_a_missing_source_folder_exits_two(tmp_path, out_dir, cap):
    assert cli.main([str(tmp_path / "nope"), str(out_dir)]) == 2
    assert "No such folder" in cap.getvalue()
    assert not out_dir.exists()  # nothing created on the way out


def test_a_source_that_is_a_file_exits_two(make_m4a, tmp_path, out_dir, cap):
    recording = make_m4a("rec.m4a", 1)
    assert cli.main([str(recording), str(out_dir)]) == 2
    assert "Not a folder" in cap.getvalue()


def test_output_equal_to_source_exits_two(tmp_path, cap):
    source = tmp_path / "src"
    source.mkdir()
    assert cli.main([str(source), str(source)]) == 2
    assert "must differ from the source" in cap.getvalue()


def test_missing_ffmpeg_exits_one(tmp_path, out_dir, cap, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    assert cli.main([str(tmp_path), str(out_dir)]) == 1
    text = cap.getvalue()
    assert "ffmpeg" in text and "ffprobe" in text and "Missing dependency" in text


# --- the overwrite policy --------------------------------------------------- #


@pytest.fixture
def populated(make_m4a, tmp_path, out_dir):
    """Run once, then stamp a part file so a rewrite is detectable."""
    source = tmp_path / "src"
    make_m4a("rec.m4a", 10, folder=source)
    cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS])
    stamped = out_dir / "rec-part-1.m4a"
    stamped.write_bytes(b"SENTINEL")
    return source, stamped


@pytest.mark.ffmpeg
def test_on_existing_skip_leaves_the_files_alone(populated, out_dir, cap):
    source, stamped = populated
    assert cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS, "--on-existing", "skip"]) == 0
    assert stamped.read_bytes() == b"SENTINEL"
    assert "SKIPPED" in cap.getvalue()


@pytest.mark.ffmpeg
def test_on_existing_overwrite_rewrites_them(populated, out_dir, cap):
    source, stamped = populated
    code = cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS, "--on-existing", "overwrite"])
    assert code == 0
    assert stamped.read_bytes() != b"SENTINEL"
    assert duration_of(stamped) == pytest.approx(3.0, abs=0.1)


@pytest.mark.ffmpeg
def test_on_existing_abort_writes_nothing(populated, out_dir, cap):
    source, stamped = populated
    assert cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS, "--on-existing", "abort"]) == 1
    assert stamped.read_bytes() == b"SENTINEL"
    assert "nothing was written" in cap.getvalue()


@pytest.mark.ffmpeg
def test_the_chosen_policy_is_echoed_for_the_log(populated, out_dir, cap):
    source, _ = populated
    cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS, "--on-existing", "skip"])
    assert "already exist" in cap.getvalue()
    assert "skip (--on-existing)" in cap.getvalue()


@pytest.mark.ffmpeg
def test_an_untouched_output_folder_never_asks(make_m4a, tmp_path, out_dir, cap, answers):
    answers([])  # any prompt would raise EOFError
    make_m4a("rec.m4a", 4, folder=tmp_path / "src")
    assert cli.main([str(tmp_path / "src"), str(out_dir), *THREE_SECOND_PARTS]) == 0


@pytest.mark.ffmpeg
def test_a_collision_without_the_flag_stops_an_unattended_run(populated, out_dir, cap, answers):
    source, _ = populated
    answers([])  # stdin is closed
    assert cli.main([str(source), str(out_dir), *THREE_SECOND_PARTS]) == 2
    assert "--on-existing" in cap.getvalue()


# --- interactive mode ------------------------------------------------------- #


@pytest.mark.ffmpeg
def test_both_folders_can_come_from_prompts(make_m4a, tmp_path, out_dir, cap, answers):
    source = tmp_path / "src"
    make_m4a("rec.m4a", 4, folder=source)
    asked = answers([str(source), str(out_dir)])
    assert cli.main([*THREE_SECOND_PARTS]) == 0
    assert len(parts(out_dir)) == 2
    assert "Source folder" in asked[0] and "Output folder" in asked[1]


@pytest.mark.ffmpeg
def test_the_output_folder_alone_can_be_prompted(make_m4a, tmp_path, out_dir, cap, answers):
    source = tmp_path / "src"
    make_m4a("rec.m4a", 4, folder=source)
    asked = answers([str(out_dir)])
    assert cli.main([str(source), *THREE_SECOND_PARTS]) == 0
    assert len(asked) == 1 and "Output folder" in asked[0]


@pytest.mark.ffmpeg
def test_a_bad_answer_is_re_asked_in_red(make_m4a, tmp_path, out_dir, cap, answers):
    source = tmp_path / "src"
    make_m4a("rec.m4a", 4, folder=source)
    asked = answers(["", str(tmp_path / "nope"), str(source), str(out_dir)])
    assert cli.main([*THREE_SECOND_PARTS]) == 0
    assert len(asked) == 4  # three attempts at the source, then the output
    text = cap.getvalue()
    assert "Please enter a path" in text and "No such folder" in text


@pytest.mark.ffmpeg
def test_the_overwrite_question_is_asked_once_when_interactive(populated, out_dir, cap, answers):
    source, stamped = populated
    asked = answers([str(source), str(out_dir), "skip"])
    assert cli.main([*THREE_SECOND_PARTS]) == 0
    assert "Overwrite them" in asked[-1]
    assert stamped.read_bytes() == b"SENTINEL"


def test_a_prompt_with_no_input_explains_the_flags(tmp_path, cap, answers):
    answers([])
    assert cli.main([]) == 2
    assert "--on-existing" in cap.getvalue()


def test_ctrl_c_returns_130(tmp_path, cap, monkeypatch):
    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "resolve_source", interrupt)
    assert cli.main([]) == 130
    assert "Aborted" in cap.getvalue()
