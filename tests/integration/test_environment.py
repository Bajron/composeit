import subprocess
import os
from .utils import *


def test_environment_expansions(process_cleaner):
    try:
        os.environ["FROM_ENV"] = "bar"
        os.environ["FROM_ENV_OVERRIDEN"] = "bar"

        service_directory = tests_directory / "projects" / "expansions"
        up = subprocess.Popen(
            ["composeit", "up", "--no-start"],
            cwd=service_directory,
        )

        subprocess.check_call(
            ["composeit", "server_info", "--wait", "--wait-timeout", "5"],
            cwd=service_directory,
        )

        logs = LogsGatherer(
            service_directory, ["env_test_map", "env_test_list", "env_test_suppress"]
        )
        subprocess.call(["composeit", "start"], cwd=service_directory)

        subprocess.call(["composeit", "down"], cwd=service_directory)
        logs.join()

        # Values are the same for all, they are evaluated in compose environment
        values = [
            "ENV_BY_MAP=",
            "ENV_INT_MAP=",
            "ENV_BY_LIST=",
            "ENV_INT_LIST=",
            "COPIED_FROM_ENV=",
            "VERBATIM_FROM_ENV=",
            "FROM_DOT_ENV=foo",
            "FROM_ENV=bar",
            "DOT_AND_ENV=foo and bar",
            "NOT_EXPANDED=${FROM_DOT_ENV}",
            "EMPTY=",
            "COMPOSEIT_NOT_EXISTENT=",
            "FROM_ENV_SAVED=bar",
            "FROM_ENV_OVERRIDEN=baz",
        ]

        env_map_out = [
            "Values:",
            *values,
            "Variables:",
            "ENV_BY_MAP=from map",
            "ENV_INT_MAP=1",
            "ENV_BY_LIST=None",
            "ENV_INT_LIST=None",
            "FROM_DOT_ENV=foo",
            "FROM_ENV=bar",
            "DOT_AND_ENV=foo and bar",
            "NOT_EXPANDED=${FROM_DOT_ENV}",
            "EMPTY=",
            "COMPOSEIT_NOT_EXISTENT=None",
            "FROM_ENV_SAVED=bar",
            "FROM_ENV_OVERRIDEN=zaz",
            "COPIED_FROM_ENV=copy: bar",
            "VERBATIM_FROM_ENV=verbatim: ${FROM_ENV}",
            "Input:",
        ]

        assert env_map_out == logs.get_service("env_test_map")

        env_list_out = [
            "Values:",
            *values,
            "Variables:",
            "ENV_BY_MAP=None",
            "ENV_INT_MAP=None",
            "ENV_BY_LIST=from list",
            "ENV_INT_LIST=2",
            "FROM_DOT_ENV=foo",
            "FROM_ENV=bar",
            "DOT_AND_ENV=foo and bar",
            "NOT_EXPANDED=${FROM_DOT_ENV}",
            "EMPTY=",
            "COMPOSEIT_NOT_EXISTENT=None",
            "FROM_ENV_SAVED=bar",
            "FROM_ENV_OVERRIDEN=zaz",
            "COPIED_FROM_ENV=copy bar",
            "VERBATIM_FROM_ENV=verbatim ${FROM_ENV}",
            "Input:",
        ]

        assert env_list_out == logs.get_service("env_test_list")

        env_suppress_out = [
            "Values:",
            *values,
            "Variables:",
            "ENV_BY_MAP=None",
            "ENV_INT_MAP=None",
            "ENV_BY_LIST=from list",
            "ENV_INT_LIST=3",
            "FROM_DOT_ENV=None",
            "FROM_ENV=None",
            "DOT_AND_ENV=None",
            "NOT_EXPANDED=None",
            "EMPTY=None",
            "COMPOSEIT_NOT_EXISTENT=None",
            "FROM_ENV_SAVED=bar",
            "FROM_ENV_OVERRIDEN=zzz",
            "COPIED_FROM_ENV=copy bar",
            "VERBATIM_FROM_ENV=verbatim ${FROM_ENV}",
            "Input:",
        ]
        assert env_suppress_out == logs.get_service("env_test_suppress")

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
