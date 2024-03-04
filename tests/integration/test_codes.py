import subprocess

from .utils import *


def test_abort_on_exit(process_cleaner):
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        up = subprocess.Popen(["composeit", "up", "--abort-on-service-exit"], cwd=service_directory)
        process_cleaner.append(up)

        up.wait(5)
        assert up.returncode != None
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_no_abort_on_exit(process_cleaner):
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        up = subprocess.Popen(
            ["composeit", "up", "simple3", "--abort-on-service-exit"], cwd=service_directory
        )
        process_cleaner.append(up)
        with pytest.raises(subprocess.TimeoutExpired):
            up.wait(1)
        assert up.returncode == None
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_return_code(process_cleaner):
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        up = subprocess.Popen(
            [
                "composeit",
                "up",
                "simple2",
                "simple3",
                "--abort-on-service-exit",
                "--exit-code-from",
                "simple2",
            ],
            cwd=service_directory,
        )
        process_cleaner.append(up)

        up.wait(5)
        assert up.returncode == 22
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
