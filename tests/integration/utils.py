import pathlib
import psutil
import subprocess
import pytest
import time
import io
import re
import os
import threading
import locale
from typing import List

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
        print(f"Problem in kill_deepest_child: {ex}")


def is_sequence(s: List[int]):
    """Verifies if the provided list is an integer sequence incrementing by 1"""
    return len(s) > 0 and all(map(lambda x: x == 1, [a - b for a, b in zip(s[1:], s[:-1])]))


@pytest.fixture()
def process_cleaner():
    to_clean: List[subprocess.Popen] = []
    yield to_clean
    for process in to_clean:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
            continue

        # https://stackoverflow.com/questions/74312272/how-can-i-fail-tests-in-a-teardown-fixture-properly-in-pytest
        # Failing the test would be good...

        print("process_cleaner: Terminating", process)
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
                print("process_cleaner: Terminating", child)
                child.terminate()

        pytest.fail("Process alive on teardown", process.args)


def ps_split_to_state(split):
    for key in [
        "Up",
        "exited",
        "terminated",
        "restarting",
        "stopped",
        "terminating",
        "stopping",
        "starting",
    ]:
        if key in split:
            return key.lower()
    return None


def ps(service_directory, *args, services=None):
    header_lines = 2
    ps_output = subprocess.check_output(
        ["composeit", *[str(a) for a in args], "ps", *(services or [])], cwd=service_directory
    )
    ps_lines = [l.decode().strip() for l in io.BytesIO(ps_output).readlines()]
    if (
        len(ps_lines) < 2
        or not ps_lines[0].startswith("Project")
        or not ps_lines[1].startswith("NAME")
    ):
        # Bad call, not even receiving a header
        return None
    states = {sp[0]: ps_split_to_state(sp) for sp in [ps.split() for ps in ps_lines[header_lines:]]}
    return states


def wait_for_ps(service_directory, *args, services=None, timeout=30):
    end_time = time.time() + timeout
    states = None
    print(args)
    while states is None and time.time() < end_time:
        states = ps(service_directory, *args, services=services)
        time.sleep(0.0625)
    return states


def top(service_directory, *args, services=None):
    header_lines = 2
    top_output = subprocess.check_output(
        ["composeit", *[str(a) for a in args], "top", *(services or [])], cwd=service_directory
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


class LogsGatherer:
    def __init__(self, service_directory, services=None, marker_filter=":") -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        args_services = services or []
        self.process = subprocess.Popen(
            [
                "composeit",
                "logs",
                *([] if marker_filter and len(args_services) == 1 else args_services),
            ],
            stdout=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        self.log_out = None
        self.log_on = threading.Semaphore(0)

        if marker_filter and services is not None:
            self.reading_thread = threading.Thread(
                target=self.read_filtered, args=(services, marker_filter)
            )
        else:
            self.reading_thread = threading.Thread(target=self.read_unfiltered)
        self.reading_thread.start()

    def get_service_ints(self, service):
        return list(map(lambda x: int(x[1]), filter(lambda x: x[0] == service, self.log_out)))

    def get_service(self, service):
        return list(x[1] for x in filter(lambda x: x[0] == service, self.log_out))

    def read_filtered(self, services, marker_filter):
        self.log_out = []
        services = "|".join(services)
        marker_filter = f"[{marker_filter}]" if marker_filter else ""
        log_line = re.compile(f"^(?P<service>{services}){marker_filter}\\s+(?P<log>.*)$")
        encoding = locale.getpreferredencoding(False)
        self.log_on.release()
        for l in [
            l.decode(encoding, errors="replace").strip() for l in self.process.stdout.readlines()
        ]:
            m = log_line.match(l)
            if not m:
                continue
            self.log_out.append((m["service"], m["log"].strip()))

    def read_unfiltered(self):
        self.log_out = []
        encoding = locale.getpreferredencoding(False)
        self.log_on.release()
        for l in [
            l.decode(encoding, errors="replace").strip() for l in self.process.stdout.readlines()
        ]:
            self.log_out.append(l)

    def stop(self):
        kill_deepest_child(self.process.pid)
        self.join()

    def join(self):
        self.reading_thread.join()


class ShowLogs:
    def __init__(self, stream) -> None:
        self.stream = stream
        self.thread = threading.Thread(target=self.show)
        self.thread.start()
        self.run = True

    def show(self):
        encoding = locale.getpreferredencoding(False)
        line = self.stream.readline()
        while line and self.run:
            print(line.decode(encoding, errors="replace").strip(), flush=True)
            line = self.stream.readline()

    def stop(self):
        self.run = False
        self.thread.join()
