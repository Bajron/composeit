import subprocess
import psutil
import io
import os

from .utils import *


def test_up_simple_down_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
    process_cleaner.append(up)

    wait_for_server_line(up)

    subprocess.call(["composeit", "down"], cwd=service_directory)

    rc = up.wait(5)
    assert rc is not None


def test_blocking_up():
    service_directory = tests_directory / "projects" / "simple"
    try:
        code = subprocess.call(
            ["composeit", "up", "--wait", "--wait-timeout", "10"],
            cwd=service_directory,
        )
        assert code == 0

        services = ps(service_directory)
        assert all(s == "up" for s in services.values())
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_blocking_up_single_service():
    service_directory = tests_directory / "projects" / "simple"
    try:
        code = subprocess.call(
            ["composeit", "up", "simple1", "--wait", "--wait-timeout", "10"],
            cwd=service_directory,
        )
        assert code == 0

        services = ps(service_directory)
        assert services["simple1"] == "up"
        assert services["simple2"] != "up"
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)


def test_diagnostic_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        wait_for_server_line(up)

        header_lines = 2
        services = 2
        ps_output = subprocess.check_output(["composeit", "ps"], cwd=service_directory)
        ps_lines = [l.decode().strip() for l in io.BytesIO(ps_output).readlines()]
        assert len(ps_lines) == (header_lines + services)
        assert ps_lines[0].startswith("Project: simple")
        for ps in ps_lines[header_lines:]:
            assert "python" in ps
            assert "Up " in ps
            assert any([s in ps for s in ["simple1", "simple2"]])

        top_output = subprocess.check_output(["composeit", "top"], cwd=service_directory)
        top_lines = [l.decode().strip() for l in io.BytesIO(top_output).readlines()]
        # There might be more processes due to Python delegating binaries on Windows
        # For example Python from virtual env opens the global Python
        assert len(top_lines) >= (header_lines + services)
        assert top_lines[0].startswith("Project: simple")
        for top in top_lines[header_lines:]:
            assert "python" in top
            parts = top.split()
            ppid = int(parts[2])
            assert ppid == up.pid or up.pid in [p.pid for p in psutil.Process(ppid).parents()]

        # This should stop services, but keep server running
        stop_output = subprocess.check_output(
            ["composeit", "stop", "simple1", "simple2"], cwd=service_directory
        )
        stop_lines = [l.decode().strip() for l in io.BytesIO(stop_output).readlines()]
        for line in stop_lines:
            assert any([s in line for s in ["simple1", "simple2"]])
            assert "Stopped" in line

        # We can list the services as stopped/exited
        ps_output = subprocess.check_output(["composeit", "ps"], cwd=service_directory)
        ps_lines = [l.decode().strip() for l in io.BytesIO(ps_output).readlines()]
        assert len(ps_lines) == (header_lines + services)
        for ps in ps_lines[header_lines:]:
            assert "python" in ps
            assert "exited " in ps
            assert any([s in ps for s in ["simple1", "simple2"]])

        # There should be no processes
        top_output = subprocess.check_output(["composeit", "top"], cwd=service_directory)
        top_lines = [l.decode().strip() for l in io.BytesIO(top_output).readlines()]
        assert len(top_lines) == header_lines
        assert top_lines[0].startswith("Project: simple")

        # This closes the server (no need down, as simple does not have preparation step)
        subprocess.call(["composeit", "stop"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None

        # Diagnosting does not work without server
        # TODO? In theory we could display the services as not started
        ps_done = subprocess.run(["composeit", "ps"], capture_output=True, cwd=service_directory)
        assert "Server is not running" in ps_done.stderr.decode(errors="replace")

        top_done = subprocess.run(["composeit", "top"], capture_output=True, cwd=service_directory)
        assert "Server is not running" in top_done.stderr.decode(errors="replace")
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_logs_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        wait_for_server_line(up)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        log_all = subprocess.Popen(
            ["composeit", "logs", "--follow"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log_all)

        log_1 = subprocess.Popen(
            ["composeit", "logs", "--follow", "simple1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log_1)

        log_2 = subprocess.Popen(
            ["composeit", "logs", "--follow", "simple2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log_2)

        attach_for_log = subprocess.Popen(
            ["composeit", "attach", "simple2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(attach_for_log)

        assert log_all.stdout is not None
        out = [log_all.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_all.pid)
        s1 = [int(s.replace("simple1:", "").strip()) for s in out if "simple1" in s]
        s2 = [int(s.replace("simple2:", "").strip()) for s in out if "simple2" in s]

        assert is_sequence(s1)
        assert is_sequence(s2)

        # Single log and attachment provides raw input

        assert log_1.stdout is not None
        out = [log_1.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_1.pid)
        s1 = [int(s.strip()) for s in out]
        assert is_sequence(s1)

        assert log_2.stdout is not None
        out = [log_2.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_2.pid)
        s2 = [int(s.strip()) for s in out]
        assert is_sequence(s2)

        assert attach_for_log.stdout is not None
        out = [attach_for_log.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(attach_for_log.pid)
        s2 = [int(s.strip()) for s in out]
        assert is_sequence(s2)
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None

        log_all.wait(5)
        log_1.wait(5)
        log_2.wait(5)
        attach_for_log.wait(5)


def test_start_by_pointed_file(process_cleaner):
    try:
        service_directory = tests_directory / "projects" / "simple"
        service_file = service_directory / "composeit.yml"

        # Note no CWD set in here
        up = subprocess.Popen(["composeit", "-f", str(service_file), "up"], stdout=subprocess.PIPE)
        wait_for_server_line(up)

        services = ps(tests_directory)
        assert services is None

        services = ps(service_directory)
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        subprocess.call(
            ["composeit", "--project-directory", str(service_directory), "down", "simple1"]
        )
        services = ps(service_directory)
        assert services["simple1"] == "exited"
        assert services["simple2"] == "up"

        # Project name is redundant here, but just for testing
        subprocess.call(
            [
                "composeit",
                "-f",
                str(service_file),
                "--project-name",
                service_directory.name,
                "down",
                "simple2",
            ]
        )
        services = ps(service_directory)
        assert services["simple1"] == "exited"
        assert services["simple2"] == "exited"
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_start_by_file_with_name(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"
    service_file = service_directory / "composeit.yml"

    try:
        # Note no CWD set in here
        up1 = subprocess.Popen(
            ["composeit", "-f", str(service_file), "--project-name", "1", "up"],
            stdout=subprocess.DEVNULL,
        )
        process_cleaner.append(up1)
        wait_for_ps(service_directory, "--project-name", "1")

        up2 = subprocess.Popen(
            ["composeit", "-f", str(service_file), "--project-name", "2", "up"],
            stdout=subprocess.DEVNULL,
        )
        process_cleaner.append(up2)
        wait_for_ps(service_directory, "--project-name", "2")

        services = ps(tests_directory)
        assert services is None

        # No results without project name
        services = ps(service_directory)
        assert services is None

        services = ps(service_directory, "--project-name", "1")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        services = ps(service_directory, "--project-name", "2")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        # No results without project name
        subprocess.call(["composeit", "down"], cwd=service_directory)

        services = ps(service_directory, "--project-name", "1")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        services = ps(service_directory, "--project-name", "2")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        # Closing a service in project "1" leaves "2" unaffected
        subprocess.call(
            ["composeit", "--project-name", "1", "down", "-t", "4", "simple1"],
            cwd=service_directory,
        )

        services = ps(tests_directory, "-f", str(service_file), "--project-name", "1")
        assert len(services) == 2
        assert services["simple1"] == "exited"
        assert services["simple2"] == "up"

        services = ps(tests_directory, "-f", str(service_file), "--project-name", "2")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        # Close project 1, the other one should be unaffected
        subprocess.call(
            ["composeit", "--project-name", "1", "down", "-t", "4"], cwd=service_directory
        )

        services = ps(service_directory, "--project-name", "1")
        assert services is None

        # Test 3 different ways to specify the project
        services = ps(service_directory, "--project-name", "2")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        services = ps(tests_directory, "-f", str(service_file), "--project-name", "2")
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        services = ps(
            tests_directory, "--project-directory", str(service_directory), "--project-name", "2"
        )
        assert len(services) == 2
        assert services["simple1"] == "up"
        assert services["simple2"] == "up"

        subprocess.call(
            ["composeit", "--project-name", "2", "down", "-t", "4"],
            cwd=service_directory,
            timeout=10,
        )
        services = ps(service_directory, "--project-name", "2")
        assert services is None
    finally:
        subprocess.call(
            ["composeit", "--project-name", "1", "down"], cwd=service_directory, timeout=10
        )
        subprocess.call(
            ["composeit", "--project-name", "2", "down"], cwd=service_directory, timeout=10
        )
        rc = up1.wait(5)
        assert rc is not None
        rc = up2.wait(5)
        assert rc is not None
