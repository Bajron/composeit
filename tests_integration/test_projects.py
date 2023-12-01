import pathlib
import subprocess
import pytest
import psutil
import time
import io
import os
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

        def is_sequence(s):
            return len(s) > 0 and all(map(lambda x: x == 1, [a - b for a, b in zip(s[1:], s[:-1])]))

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
