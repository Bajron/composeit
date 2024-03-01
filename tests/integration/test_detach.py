import subprocess

from .utils import *


def test_up_simple_detached_down_on_side():
    service_directory = tests_directory / "projects" / "simple"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        subprocess.call(["composeit", "up", "-d"], cwd=service_directory, stdout=subprocess.PIPE)

        for _ in range(10):
            states = ps(service_directory)
            if states is not None and any([state == "up" for state in states.values()]):
                break
        assert any([state == "up" for state in states.values()])
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_up_simple_detached_down_on_side_cwd():
    service_directory = tests_directory / "projects" / "simple"
    service_file = service_directory / "composeit.yml"
    subprocess.call(["composeit", "down"], cwd=service_directory)

    try:
        subprocess.call(["composeit", "-f", str(service_file), "up", "-d"], stdout=subprocess.PIPE)

        for _ in range(10):
            states = ps(service_directory)
            if states is not None and any([state == "up" for state in states.values()]):
                break
        assert any([state == "up" for state in states.values()])
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        subprocess.call(["composeit", "-f", str(service_file), "down"])
