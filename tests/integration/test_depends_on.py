import subprocess

from .utils import *


def test_dependencies(process_cleaner):
    service_directory = tests_directory / "projects" / "depends_on"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        wait_for_server_line(up)

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


def test_restarting_dependencies(process_cleaner):
    service_directory = tests_directory / "projects" / "depends_on"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        wait_for_server_line(up)

        # Just close independent service
        subprocess.run(["composeit", "stop", "leaf"], cwd=service_directory)
        states = ps(service_directory)
        assert states["leaf"] == "exited"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        log = LogsGatherer(service_directory, ["leaf", "root"])
        log.log_on.acquire()

        # Restart crucial service, not deps, so depending service is not started
        subprocess.run(
            ["composeit", "restart", "root", "--no-deps", "--timeout", "3"], cwd=service_directory
        )
        ps_wait_for(service_directory, service="root", state="up")
        states = ps(service_directory)
        assert states["leaf"] == "exited"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        # Restart crucial service, and all depenent services
        subprocess.run(["composeit", "restart", "root", "--timeout", "0"], cwd=service_directory)
        ps_wait_for(service_directory, service="root", state="up")
        states = ps(service_directory)
        assert states["leaf"] == "up"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        subprocess.run(["composeit", "stop", "leaf"], cwd=service_directory)
        subprocess.run(["composeit", "restart"], cwd=service_directory)
        ps_wait_for(service_directory, service="root", state="up")
        states = ps(service_directory)
        assert states["leaf"] == "up"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        log.stop()
        assert log.get_service_ints("leaf").count(0) == 2
        assert log.get_service_ints("root").count(0) == 3

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_up_no_deps(process_cleaner):
    service_directory = tests_directory / "projects" / "depends_on"

    try:
        up = subprocess.Popen(
            ["composeit", "up", "leaf", "--no-deps"], cwd=service_directory, stdout=subprocess.PIPE
        )
        process_cleaner.append(up)

        ps_wait_for(service_directory, service="leaf", state="up")
        states = ps(service_directory)

        assert states["leaf"] == "up"
        assert states["middle"] != "up"
        assert states["root"] != "up"
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
