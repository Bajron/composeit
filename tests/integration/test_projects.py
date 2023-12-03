import pathlib
import subprocess
import pytest
import psutil
import time
import io
import os
import re
import threading

tests_directory = pathlib.Path(__file__).parent


# composeit processes usually spawn additional python processes
# This function intends to simulate ctrl+c for simulated interactive calls
def kill_deepest_child(pid):
    try:
        process = psutil.Process(pid)
        ch = process.children()
        while len(ch) > 0:
            process = ch[0]
            ch = process.children()
        process.terminate()
    except Exception as ex:
        print("Problem in kill_deepest_child")
        pass


def is_sequence(s: list[int]):
    """Verifies if the provided list is an integer sequence incrementing by 1"""
    return len(s) > 0 and all(map(lambda x: x == 1, [a - b for a, b in zip(s[1:], s[:-1])]))


@pytest.fixture()
def process_cleaner():
    to_clean: list[subprocess.Popen] = []
    yield to_clean
    for process in to_clean:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
            continue

        # https://stackoverflow.com/questions/74312272/how-can-i-fail-tests-in-a-teardown-fixture-properly-in-pytest
        # Failing the test would be good...

        process.terminate()

        for _ in range(10):
            running = 0
            for child in children:
                if child.is_running():
                    running += 1
                    time.sleep(0)
            if running == 0:
                break

        for child in children:
            if child.is_running():
                child.terminate()

        pytest.fail("Process alive on teardown", process.args)


def test_up_simple_down_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
    process_cleaner.append(up)

    # Note: need to wait for it to start the server
    first_line = up.stdout.readline().decode()
    assert first_line.startswith("Server created")

    subprocess.call(["composeit", "down"], cwd=service_directory)

    rc = up.wait(5)
    assert rc is not None


def test_diagnostic_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

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
        assert len(top_lines) == (header_lines + services)
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
        assert "Server is not running" in ps_done.stderr.decode()

        top_done = subprocess.run(["composeit", "top"], capture_output=True, cwd=service_directory)
        assert "Server is not running" in top_done.stderr.decode()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_logs_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        log_all = subprocess.Popen(
            ["composeit", "logs"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log_all)

        log_1 = subprocess.Popen(
            ["composeit", "logs", "simple1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log_1)

        log_2 = subprocess.Popen(
            ["composeit", "logs", "simple2"],
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

        out = [log_all.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_all.pid)
        s1 = [int(s.replace("simple1:", "").strip()) for s in out if "simple1" in s]
        s2 = [int(s.replace("simple2:", "").strip()) for s in out if "simple2" in s]

        assert is_sequence(s1)
        assert is_sequence(s2)

        # Single log and attachment provides raw input

        out = [log_1.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_1.pid)
        s1 = [int(s.strip()) for s in out]
        assert is_sequence(s1)

        out = [log_2.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(log_2.pid)
        s2 = [int(s.strip()) for s in out]
        assert is_sequence(s2)

        out = [attach_for_log.stdout.readline().decode() for _ in range(6)]
        kill_deepest_child(attach_for_log.pid)
        s2 = [int(s.strip()) for s in out]
        assert is_sequence(s2)
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_start_by_pointed_file(process_cleaner):
    try:
        service_directory = tests_directory / "projects" / "simple"
        service_file = service_directory / "composeit.yml"

        # Note no CWD set in here
        up = subprocess.Popen(["composeit", "-f", str(service_file), "up"], stdout=subprocess.PIPE)
        process_cleaner.append(up)
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

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
    try:
        service_directory = tests_directory / "projects" / "simple"
        service_file = service_directory / "composeit.yml"

        # Note no CWD set in here
        up1 = subprocess.Popen(
            ["composeit", "-f", str(service_file), "--project-name", "1", "up"],
            stdout=subprocess.PIPE,
        )
        process_cleaner.append(up1)
        up2 = subprocess.Popen(
            ["composeit", "-f", str(service_file), "--project-name", "2", "up"],
            stdout=subprocess.PIPE,
        )
        process_cleaner.append(up2)

        first_line = up1.stdout.readline().decode()
        assert first_line.startswith("Server created")

        first_line = up2.stdout.readline().decode()
        assert first_line.startswith("Server created")

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
            ["composeit", "--project-name", "1", "down", "simple1"], cwd=service_directory
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
        subprocess.call(["composeit", "--project-name", "1", "down"], cwd=service_directory)

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

    finally:
        subprocess.call(["composeit", "--project-name", "1", "down"], cwd=service_directory)
        subprocess.call(["composeit", "--project-name", "2", "down"], cwd=service_directory)
        rc = up1.wait(5)
        assert rc is not None
        rc = up2.wait(5)
        assert rc is not None


def test_attach(process_cleaner):
    service_directory = tests_directory / "projects" / "input"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)

        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        log = subprocess.Popen(
            ["composeit", "logs", "echo"],
            stdout=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(log)

        attach = subprocess.Popen(
            ["composeit", "attach", "echo"],
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(attach)

        log_out = None

        def read3():
            nonlocal log_out
            log_out = [log.stdout.readline().decode().strip() for _ in range(3)]

        t = threading.Thread(target=read3)
        t.start()

        words = ["spam", "ham", "eggs"]
        for word in words:
            attach.stdin.write(f"{word}\n".encode())
            attach.stdin.flush()
            assert word == attach.stdout.readline().decode().strip()
        kill_deepest_child(attach.pid)

        t.join()
        assert log_out == words
        kill_deepest_child(log.pid)
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def ps_split_to_state(split):
    for key in ["Up", "exited", "terminated", "restarting", "stopped"]:
        if key in split:
            return key.lower()
    return None


def ps(service_directory, *args):
    header_lines = 2
    ps_output = subprocess.check_output(
        ["composeit", *[str(a) for a in args], "ps"], cwd=service_directory
    )
    ps_lines = [l.decode().strip() for l in io.BytesIO(ps_output).readlines()]
    if (
        len(ps_lines) < 2
        or not ps_lines[0].startswith("Project")
        or not ps_lines[1].startswith("NAME")
    ):
        # Bad call, not even receiveing a header
        return None
    states = {sp[0]: ps_split_to_state(sp) for sp in [ps.split() for ps in ps_lines[header_lines:]]}
    return states


def top(service_directory, *args):
    header_lines = 2
    top_output = subprocess.check_output(
        ["composeit", *[str(a) for a in args], "top"], cwd=service_directory
    )
    top_lines = [l.decode().strip() for l in io.BytesIO(top_output).readlines()]
    if (
        len(top_lines) < 2
        or not top_lines[0].startswith("Project")
        or not top_lines[1].startswith("UID")
    ):
        # Bad call, not even receiveing a header
        return None
    return top_lines[header_lines:]


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
        assert len(top_lines) == 3

        # Just close independent service
        subprocess.check_output(["composeit", "stop", "leaf"], cwd=service_directory)
        states = ps(service_directory)
        assert states["leaf"] == "exited"
        assert states["middle"] == "up"
        assert states["root"] == "up"

        top_lines = top(service_directory)
        assert len(top_lines) == 2

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
        assert len(top_lines) == 3

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_restarting(process_cleaner):
    service_directory = tests_directory / "projects" / "restarts"

    try:
        up = subprocess.Popen(
            ["composeit", "up", "one_shot"], cwd=service_directory, stdout=subprocess.PIPE
        )
        process_cleaner.append(up)

        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        # always policy brings the service up on each server start (note we started only "one_shot")
        states = ps(service_directory)
        assert states["always"] == "up"

        subprocess.call(["composeit", "stop", "always"], cwd=service_directory)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        log_all = subprocess.Popen(
            ["composeit", "logs"],
            stdout=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        log_out = None

        def read_filtered():
            nonlocal log_out
            log_out = []
            services = "|".join(states.keys())
            log_line = re.compile(f"^(?P<service>{services}):\\s+(?P<log>.*)$")
            for l in [l.decode().strip() for l in log_all.stdout.readlines()]:
                m = log_line.match(l)
                if not m:
                    continue
                log_out.append((m["service"], m["log"].strip()))

        t = threading.Thread(target=read_filtered)
        t.start()

        subprocess.call(["composeit", "start"], cwd=service_directory)
        # TODO: better solution is welcome
        time.sleep(1)

        states = ps(service_directory)

        assert states["always"] == "up"
        # Relies on the default restart delay of 10s
        assert states["restarting"] == "restarting"
        assert states["fail_restart"] == "exited"
        assert states["not_restarting"] == "exited"
        assert states["one_shot"] == "exited"
        assert states["one_time"] == "exited"
        assert states["one_try"] == "exited"
        assert states["one_run"] == "exited"

        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)

        # Logs should stop as well because of server disconnection
        t.join()

        def get_service_logs(service):
            return list(map(lambda x: int(x[1]), filter(lambda x: x[0] == service, log_out)))

        assert is_sequence(get_service_logs("always"))
        for quick4 in ["not_restarting", "one_shot", "one_time", "one_try", "one_run"]:
            assert [0, 1, 2, 3] == get_service_logs(quick4), f"Not matching for {quick4}"

        # First run plus 3 restart attempts
        assert [0, 1, 2, 0, 1, 2, 0, 1, 2, 0, 1, 2] == get_service_logs("fail_restart")

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
