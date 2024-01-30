import subprocess

from .utils import *


def test_dependencies(process_cleaner):
    service_directory = tests_directory / "projects" / "depends_on"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        states = ps(service_directory)
        assert all(map(lambda x: x == "up", states.values()))
        top_lines = top(service_directory)
        assert len(top_lines) >= 3

        # Just close independent service
        subprocess.check_output(["composeit", "stop", "leaf"], cwd=service_directory)
        states = ps(service_directory)
        assert states["leaf"] == "exited"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        top_lines = top(service_directory)
        assert len(top_lines) >= 2

        # Close crucial service, the other one that depends on it should close too
        subprocess.check_output(["composeit", "stop", "root"], cwd=service_directory)
        states = ps(service_directory)
        assert states["leaf"] == "exited"
        assert states["middle"] == "exited"
        assert states["root"] == "exited"

        top_lines = top(service_directory)
        assert len(top_lines) == 0

        # Start service that has dependencies, others should be started as well
        subprocess.check_output(["composeit", "start", "leaf"], cwd=service_directory)
        states = ps(service_directory)
        assert states["leaf"] == "up"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        top_lines = top(service_directory)
        assert len(top_lines) >= 3

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
