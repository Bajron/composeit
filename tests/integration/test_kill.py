import subprocess
import os
import sys

from .utils import *


def test_kill(process_cleaner):
    service_directory = tests_directory / "projects" / "stopping"

    try:
        os.environ["INTERRUPT_SIGNAL"] = "CTRL_C_EVENT" if sys.platform == "win32" else "SIGINT"

        up = subprocess.Popen(
            ["composeit", "up"],
            cwd=service_directory,
        )
        subprocess.check_call(
            ["composeit", "server_info", "--wait", "--wait-timeout", "5"],
            cwd=service_directory,
        )

        log = LogsGatherer(
            service_directory,
            ["simple_term", "quick_kill_int", "long_wait"],
            marker_filter="*>:",
        )
        process_cleaner.append(log.process)

        # First down blocks (waiting for stop), so make it in the background
        down = subprocess.Popen(["composeit", "down"], cwd=service_directory)
        process_cleaner.append(down)

        ps_wait_for(
            service_directory, service="simple_term", state="terminated", tries=20, sleep=0.02
        )
        p = ps(service_directory)

        assert p["simple_term"] == "terminated"
        assert p["long_wait"] == "terminating"
        time.sleep(1)
        p = ps(service_directory)
        assert p["quick_kill_int"] == "terminated"
        assert p["long_wait"] == "terminating"

        # Still waiting for the down
        assert down.poll() is None

        # Second "down" triggers kill
        subprocess.call(["composeit", "down"], cwd=service_directory)

        for _ in range(10):
            p = ps(service_directory)
            if p is None:
                break
            time.sleep(0.01)

        log.stop()
        st = log.get_service("simple_term")
        print(st)
        assert not any("Killing the process because of timeout" in x for x in st)
        qk = log.get_service("quick_kill_int")
        print(qk)
        assert any("Killing the process because of timeout" in x for x in qk)
        lw = log.get_service("long_wait")
        print(lw)
        assert any("Killing the process, force kill triggered" in x for x in lw)

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
        rc = log.process.wait(5)
        assert rc is not None
        rc = down.wait(5)
        assert rc is not None
