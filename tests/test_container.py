"""Container mode: the mount points stand in for the prompts.

These run on the host — no Docker needed. The Dockerfile is only read as text,
to catch it drifting apart from the constants the code relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from call_recording_splitter import cli

from .conftest import needs_ffmpeg

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


@pytest.fixture
def containerised(monkeypatch, tmp_path):
    """Pretend to be inside the image, with the mounts pointed at tmp_path."""
    source = tmp_path / "input"
    output = tmp_path / "output"
    source.mkdir()
    monkeypatch.setenv(cli.CONTAINER_FLAG, "1")
    monkeypatch.setattr(cli, "CONTAINER_INPUT", str(source))
    monkeypatch.setattr(cli, "CONTAINER_OUTPUT", str(output))
    return source, output


# --- detecting the container ------------------------------------------------ #


def test_not_in_a_container_by_default(monkeypatch):
    monkeypatch.delenv(cli.CONTAINER_FLAG, raising=False)
    assert cli.in_container() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything"])
def test_a_set_flag_means_container(monkeypatch, value):
    monkeypatch.setenv(cli.CONTAINER_FLAG, value)
    assert cli.in_container() is True


@pytest.mark.parametrize("value", ["", "  ", "0", "false", "no", "FALSE"])
def test_an_explicitly_off_flag_does_not(monkeypatch, value):
    monkeypatch.setenv(cli.CONTAINER_FLAG, value)
    assert cli.in_container() is False


# --- the mount points replace the prompts ----------------------------------- #


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_no_arguments_means_the_mounts_and_no_prompts(containerised, make_m4a, cap, answers):
    source, output = containerised
    answers([])  # any prompt at all would raise EOFError
    make_m4a("rec.m4a", 4, folder=source)
    assert cli.main(["--minutes", "0.05"]) == 0
    assert len(list(output.glob("rec-part-*.m4a"))) == 2


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_the_mounts_in_use_are_announced(containerised, make_m4a, cap):
    source, output = containerised
    make_m4a("rec.m4a", 2, folder=source)
    cli.main(["--minutes", "0.05"])
    text = cap.getvalue()
    assert "Container mode" in text and str(source) in text and str(output) in text


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_explicit_folders_still_win_inside_a_container(
    containerised, make_m4a, tmp_path, cap, answers
):
    _, default_output = containerised
    answers([])
    elsewhere = tmp_path / "elsewhere"
    chosen = tmp_path / "chosen"
    make_m4a("rec.m4a", 2, folder=elsewhere)
    assert cli.main([str(elsewhere), str(chosen), "--minutes", "0.05"]) == 0
    assert list(chosen.glob("rec-part-*.m4a"))
    assert not default_output.exists()


@needs_ffmpeg
@pytest.mark.ffmpeg
def test_the_other_flags_still_apply(containerised, make_m4a, cap):
    source, output = containerised
    make_m4a("rec.m4a", 10, folder=source)
    cli.main(["--minutes", "0.1"])  # 6s parts
    assert len(list(output.glob("rec-part-*.m4a"))) == 2


def test_outside_a_container_the_prompts_are_still_used(monkeypatch, tmp_path, cap, answers):
    monkeypatch.delenv(cli.CONTAINER_FLAG, raising=False)
    asked = answers([])
    assert cli.main([]) == 2
    assert asked and "Source folder" in asked[0]


# --- forgotten mounts ------------------------------------------------------- #


def test_a_missing_input_mount_explains_the_docker_flags(monkeypatch, tmp_path, cap):
    monkeypatch.setenv(cli.CONTAINER_FLAG, "1")
    monkeypatch.setattr(cli, "CONTAINER_INPUT", str(tmp_path / "absent"))
    monkeypatch.setattr(cli, "CONTAINER_OUTPUT", str(tmp_path / "output"))
    assert cli.main([]) == 2
    text = cap.getvalue()
    assert "No such folder" in text and "-v /path/to/calls:/data/input" in text


def test_an_empty_input_mount_explains_the_docker_flags(containerised, cap):
    assert cli.main([]) == 1
    text = cap.getvalue()
    assert "No .m4a files found" in text and "docker run" in text


def test_no_docker_hint_leaks_into_a_normal_run(tmp_path, monkeypatch, cap):
    monkeypatch.delenv(cli.CONTAINER_FLAG, raising=False)
    source = tmp_path / "src"
    source.mkdir()
    assert cli.main([str(source), str(tmp_path / "out")]) == 1
    assert "docker run" not in cap.getvalue()


# --- the image and the code must agree -------------------------------------- #


@pytest.mark.skipif(not DOCKERFILE.exists(), reason="Dockerfile not in this checkout")
def test_the_dockerfile_sets_the_flag_the_code_reads():
    assert f"{cli.CONTAINER_FLAG}=1" in DOCKERFILE.read_text()


@pytest.mark.skipif(not DOCKERFILE.exists(), reason="Dockerfile not in this checkout")
def test_the_dockerfile_creates_the_folders_the_code_defaults_to():
    text = DOCKERFILE.read_text()
    assert cli.CONTAINER_INPUT in text
    assert cli.CONTAINER_OUTPUT in text


@pytest.mark.skipif(not DOCKERFILE.exists(), reason="Dockerfile not in this checkout")
def test_the_image_does_not_run_as_root():
    assert "USER app" in DOCKERFILE.read_text()
