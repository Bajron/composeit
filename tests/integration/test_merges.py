import subprocess

from .utils import *


def test_merging_separate_services(process_cleaner):
    try:
        service_directory = tests_directory / "projects" / "merge"
        s1 = service_directory / "s1.yml"
        s2 = service_directory / "s2.yml"
        args = ["-f", str(s1), "-f", str(s2)]
        up = subprocess.Popen(
            ["composeit", *args, "up"], cwd=service_directory, stdout=subprocess.PIPE
        )
        wait_for_server_line(up)

        # NOTE: no config files passed to ps
        states = ps(service_directory)
        assert len(states) == 2
        assert states["simple1"] == "up"
        assert states["simple2"] == "up"

    finally:
        subprocess.call(["composeit", *args, "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


@pytest.mark.parametrize(
    "f1,f2,print_sequence",
    (
        (
            "env_list.yml",
            "env_map.yml",
            ["ENV_BY_LIST=by list", "ENV_BY_MAP=by map"],
        ),
        (
            "env_map.yml",
            "env_list.yml",
            ["ENV_BY_MAP=by map", "ENV_BY_LIST=by list"],
        ),
    ),
)
def test_merging_env_and_args(process_cleaner, f1, f2, print_sequence):
    try:
        service_directory = tests_directory / "projects" / "merge"
        s1 = service_directory / "env.yml"
        s2 = service_directory / f1
        s3 = service_directory / f2
        args = ["-f", str(s1), "-f", str(s2), "-f", str(s3)]
        up = subprocess.Popen(
            ["composeit", *args, "start", "--no-start"],
            cwd=service_directory,
        )
        process_cleaner.append(up)

        wait_for_ps(service_directory)

        log = LogsGatherer(service_directory, ["env"])
        subprocess.call(["composeit", *args, "start"], cwd=service_directory)

        ps_wait_for(service_directory, service="env", state="exited")

        subprocess.call(["composeit", *args, "down"], cwd=service_directory)
        up.wait(5)

        log.stop()
        logs = log.get_service("env")

        env_list_out = [
            "Values:",
            "echo echo",
            "Variables:",
            *print_sequence,
            "Input:",
        ]
        assert env_list_out == logs
    finally:
        subprocess.call(["composeit", *args, "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
