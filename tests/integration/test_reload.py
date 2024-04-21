import subprocess
import tempfile
import pathlib
from .utils import *


def test_reload(process_cleaner):
    with tempfile.TemporaryDirectory("composeit_test_reload") as test_directory:
        service_directory = pathlib.Path(test_directory)
        here = pathlib.Path(__file__).parent
        counter_app = str(here / "counter.py")
        try:
            comopseit_file = service_directory / "composeit.yml"
            comopseit_file.write_text(
                f"""
services:
  simple1:
    command: [python, '{counter_app}', --time, '0.1']
  simple2:
    command: [python, '{counter_app}', --time, '0.1']
"""
            )

            up = subprocess.Popen(["composeit", "up"], cwd=service_directory)
            process_cleaner.append(up)

            states = ps_wait(
                service_directory, until=lambda s: all([state == "up" for state in s.values()])
            )
            assert len(states) == 2

            subprocess.call(["composeit", "reload"], cwd=service_directory)
            states = ps(service_directory)
            assert len(states) == 2

            comopseit_file.write_text(
                f"""
services:
  simple1:
    command: [python, '{counter_app}', --time, '0.1']
  simple2:
    command: [python, '{counter_app}', --time, '0.1']
  simple3:
    command: [python, '{counter_app}', --time, '0.1']
"""
            )

            subprocess.call(["composeit", "reload"], cwd=service_directory)
            states = ps(service_directory)
            assert len(states) == 3
            assert "simple3" in states

            comopseit_file.write_text(
                f"""
services:
  simple1:
    command: [python, '{counter_app}', --time, '0.1']
  simple2:
    command: [python, '{counter_app}', --time, '0.1']
"""
            )

            subprocess.call(["composeit", "reload"], cwd=service_directory)
            states = ps(service_directory)
            assert len(states) == 3
            assert "simple3" in states
            assert all([state == "up" for state in states.values()])

        finally:
            subprocess.call(["composeit", "down"], cwd=service_directory)
            rc = up.wait(5)
            assert rc is not None
