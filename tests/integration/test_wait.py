import subprocess

from .utils import *


def test_wait_for_all():
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        subprocess.call(["composeit", "up", "-d", "--wait"], cwd=service_directory)
        wait = subprocess.Popen(["composeit", "wait"], cwd=service_directory)
        wait.wait(5)
        assert wait.returncode == 0

        services = ps(service_directory)
        assert services["simple2"] != "up"
        assert services["simple3"] == "up"
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_wait_for_single():
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        subprocess.call(["composeit", "up", "-d", "--wait"], cwd=service_directory)
        wait = subprocess.Popen(["composeit", "wait", "simple2"], cwd=service_directory)
        wait.wait(5)
        assert wait.returncode == 0

        services = ps(service_directory)
        assert services["simple2"] != "up"
        assert services["simple3"] == "up"
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_wait_for_single_and_down():
    service_directory = tests_directory / "projects" / "codes"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        subprocess.call(["composeit", "up", "-d", "--wait"], cwd=service_directory)
        wait = subprocess.Popen(
            ["composeit", "wait", "simple2", "--down-project"], cwd=service_directory
        )
        wait.wait(5)
        assert wait.returncode == 0

        services = ps(service_directory)
        assert services is None or all(s != "up" for s in services)
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
