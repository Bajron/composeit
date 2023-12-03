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
        process_cleaner.append(up)
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        # TODO: commands calling server do not really need the config...
        states = ps(service_directory, *args)
        assert len(states) == 2
        assert states["simple1"] == "up"
        assert states["simple2"] == "up"

    finally:
        subprocess.call(["composeit", *args, "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
