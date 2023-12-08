import pathlib
import psutil
import subprocess
import pytest
import time
import io
import re
import os
import threading
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
        print("Problem in kill_deepest_child")
        pass


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


class LogsGatherer:
    def __init__(self, service_directory, services) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        self.log_all = subprocess.Popen(
            ["composeit", "logs"],
            stdout=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        self.log_out = None

        self.reading_thread = threading.Thread(target=self.read_filtered, args=(services,))
        self.reading_thread.start()

    def get_service_ints(self, service):
        return list(map(lambda x: int(x[1]), filter(lambda x: x[0] == service, self.log_out)))

    def get_service(self, service):
        return list(x[1] for x in filter(lambda x: x[0] == service, self.log_out))

    def read_filtered(self, services):
        self.log_out = []
        services = "|".join(services)
        log_line = re.compile(f"^(?P<service>{services}):\\s+(?P<log>.*)$")
        for l in [l.decode().strip() for l in self.log_all.stdout.readlines()]:
            m = log_line.match(l)
            if not m:
                continue
            self.log_out.append((m["service"], m["log"].strip()))

    def join(self):
        self.reading_thread.join()
