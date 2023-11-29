import pathlib
import subprocess
import pytest
import psutil
import time

tests_directory = pathlib.Path(__file__).parent


@pytest.fixture()
def process_cleaner():
    to_clean: list[subprocess.Popen] = []
    yield to_clean
    for process in to_clean:
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
            continue

        # https://stackoverflow.com/questions/74312272/how-can-i-fail-tests-in-a-teardown-fixture-properly-in-pytest
        # Failing the test would be good...

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
                child.terminate()

        pytest.fail("Process alive on teardown", process.args)


def test_up_simple_down_on_side(process_cleaner):
    service_directory = tests_directory / "projects" / "simple"

    up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
    process_cleaner.append(up)

    # Note: need to wait for it to start the server
    first_line = up.stdout.readline().decode()
    assert first_line.startswith("Creating server")

    subprocess.call(["composeit", "down"], cwd=service_directory)

    rc = up.wait(5)
    assert rc is not None
