import subprocess
import psutil
import io
import os

from .utils import *


def test_images_when_down():
    service_directory = tests_directory / "projects" / "simple"

    run = subprocess.run(["composeit", "images"], cwd=service_directory, capture_output=True)
    lines = [l.decode().strip() for l in io.BytesIO(run.stdout).readlines()]
    lines_err = [l.decode().strip() for l in io.BytesIO(run.stderr).readlines()]
    assert lines_err[0].startswith("Server is not running")

    services = 2
    header_lines = 2
    assert len(lines) == (header_lines + services)
    assert lines[0].startswith("Project: simple")
    for line in lines[header_lines:]:
        assert "python" in line
        assert any([line.startswith(s) for s in ["simple1", "simple2"]])


def test_images_when_up(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory)
        process_cleaner.append(up)
        wait_for_ps(service_directory)

        run = subprocess.run(["composeit", "images"], cwd=service_directory, capture_output=True)
        lines = [l.decode().strip() for l in io.BytesIO(run.stdout).readlines()]
        lines_err = [l.decode().strip() for l in io.BytesIO(run.stderr).readlines()]
        assert len(lines_err) == 0 or len(lines_err) == 1 and len(lines_err[0]) == 0

        services = 2
        header_lines = 2
        assert len(lines) == (header_lines + services)
        assert lines[0].startswith("Project: simple")
        for line in lines[header_lines:]:
            assert "python" in line
            assert any([line.startswith(s) for s in ["simple1", "simple2"]])
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
