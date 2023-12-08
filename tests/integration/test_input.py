import subprocess
import os

from .utils import *


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
        go_logs = threading.Semaphore(0)

        def read3():
            nonlocal log_out
            log_out = []
            go_logs.release()
            for _ in range(3):
                log_out.append(log.stdout.readline().decode().strip())

        t = threading.Thread(target=read3)
        t.start()

        words = ["spam", "ham", "eggs"]
        go_logs.acquire()
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
        rc = attach.wait(5)
        assert rc is not None
        rc = log.wait(5)
        assert rc is not None
