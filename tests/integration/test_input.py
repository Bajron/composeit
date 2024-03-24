import subprocess
import os

from .utils import *


def test_attach(process_cleaner):
    service_directory = tests_directory / "projects" / "input"

    try:
        up = subprocess.Popen(["composeit", "up", "--no-start"], cwd=service_directory)
        process_cleaner.append(up)
        subprocess.check_call(
            ["composeit", "server_info", "--wait", "--wait-timeout", "5"],
            cwd=service_directory,
        )

        log = LogsGatherer(service_directory, ["echo"], marker_filter="")
        process_cleaner.append(log.process)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        subprocess.call(["composeit", "up"], cwd=service_directory)

        attach = subprocess.Popen(
            ["composeit", "attach", "echo"],
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=service_directory,
            env=env,
        )
        process_cleaner.append(attach)
        assert attach.stdin is not None
        assert attach.stdout is not None

        words = ["spam", "ham", "eggs"]
        log.log_on.acquire()
        for word in words:
            attach.stdin.write(f"{word}\n".encode())
            attach.stdin.flush()
            assert word == attach.stdout.readline().decode().strip()
        kill_deepest_child(attach.pid)

        log.stop()
        assert log.log_out is not None
        assert log.log_out[-3:] == words
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
        rc = attach.wait(5)
        assert rc is not None
        rc = log.process.wait(5)
        assert rc is not None
