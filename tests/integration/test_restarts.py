import subprocess

from .utils import *


def test_restarting(process_cleaner):
    service_directory = tests_directory / "projects" / "restarts"

    try:
        up = subprocess.Popen(
            ["composeit", "--verbose", "up", "one_shot"],
            cwd=service_directory,
            stdout=subprocess.PIPE,
        )
        process_cleaner.append(up)
        wait_for_server_line(up)
        ShowLogs(up.stdout)

        # always policy brings the service up on each server start (note we started only "one_shot")
        ps_wait_for(service_directory, service="always", state="up")
        states = ps(service_directory)
        assert states["always"] == "up"

        subprocess.call(["composeit", "stop", "always"], cwd=service_directory)

        logs = LogsGatherer(service_directory, states.keys())
        logs.log_on.acquire()
        subprocess.call(["composeit", "start"], cwd=service_directory)

        ps_wait_for(service_directory, service="fail_restart", state="exited", sleep=0.1, tries=20)
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

        # Verifying that "always" and "on-failure" can be stopped
        subprocess.call(["composeit", "stop", "always", "restarting"], cwd=service_directory)
        for _ in range(20):
            states = ps(service_directory, services=["always", "restarting"])
            if states and all(v == "exited" for v in states.values()):
                break
            time.sleep(0.1)
        assert states["always"] == "exited"
        assert states["restarting"] == "exited"

        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)

        # Logs should stop as well because of server disconnection
        logs.join()

        assert is_sequence(logs.get_service_ints("always"))
        for quick4 in ["not_restarting", "one_shot", "one_time", "one_try", "one_run"]:
            assert [0, 1, 2, 3] == logs.get_service_ints(quick4), f"Not matching for {quick4}"

        # TODO: sometimes there is more here... that is why this one goes with --verbose
        # First run plus 2 restart attempts
        assert [0, 1, 2, 0, 1, 2, 0, 1, 2] == logs.get_service_ints("fail_restart")

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
