import subprocess
import os
from .utils import *


def test_merging_separate_services(process_cleaner):
    try:
        os.environ["FROM_ENV"] = "bar"
        os.environ["FROM_ENV_OVERRIDEN"] = "bar"

        service_directory = tests_directory / "projects" / "expansions"
        up = subprocess.Popen(
            ["composeit", "up", "--no-start"], cwd=service_directory, stdout=subprocess.PIPE
        )
        process_cleaner.append(up)
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        logs = LogsGatherer(service_directory, ["env_test1", "env_test2"])
        subprocess.call(["composeit", "start"], cwd=service_directory)

        subprocess.call(["composeit", "down"], cwd=service_directory)
        logs.join()

        # TODO: fill in the test

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
