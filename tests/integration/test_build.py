import subprocess

from .utils import *


def test_build_single(process_cleaner):
    service_directory = tests_directory / "projects" / "build"
    build_file = service_directory / "message_leaf.tmp"
    root_build_file = service_directory / "message_root.tmp"
    try:
        up: subprocess.Popen = None
        assert not build_file.exists()
        assert not root_build_file.exists()

        up = subprocess.Popen(
            ["composeit", "start", "--no-start"], cwd=service_directory, stdout=subprocess.PIPE
        )
        process_cleaner.append(up)
        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")
        ShowLogs(up.stdout)

        states = ps(service_directory)
        assert all(v == "stopped" for v in states.values())

        # Start does not run prepare
        log = LogsGatherer(service_directory, ["leaf"])
        log.log_on.acquire()
        subprocess.call(["composeit", "start", "leaf"], cwd=service_directory)
        while len(top(service_directory, services=["leaf"])) > 0:
            time.sleep(0.1)

        log.stop()
        logs = log.get_service("leaf")

        assert not any("foo" in l for l in logs)
        assert len(logs) == 0
        # `root` started, but did not cbuild
        assert not root_build_file.exists()

        # Stop both to trigger build also for `root`
        subprocess.call(["composeit", "stop", "leaf", "root"], cwd=service_directory)

        log = LogsGatherer(service_directory, ["leaf"])
        log.log_on.acquire()
        subprocess.call(["composeit", "up", "leaf"], cwd=service_directory)

        for _ in range(10):
            states = ps(service_directory, services=["leaf"])
            if states and states["leaf"] == "exited":
                break
            time.sleep(0.1)

        log.stop()
        logs = log.get_service("leaf")
        assert any("foo" in l for l in logs)
        assert len(logs) > 0

        subprocess.call(["composeit", "stop", "leaf"], cwd=service_directory)
        assert build_file.exists()
        # `root` should be prepared as a dependency on "up"
        assert root_build_file.exists()

        subprocess.call(["composeit", "down", "leaf"], cwd=service_directory)
        assert not build_file.exists()
        # `root` can still run
        assert root_build_file.exists()

        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
        assert not root_build_file.exists()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        if up:
            up.wait(5)

        if build_file.exists():
            os.unlink(build_file)
        if root_build_file.exists():
            os.unlink(root_build_file)


def test_build_on_up(process_cleaner):
    service_directory = tests_directory / "projects" / "build"
    build_file = service_directory / "message_leaf.tmp"
    root_build_file = service_directory / "message_root.tmp"

    try:
        up: subprocess.Popen = None
        assert not build_file.exists()
        assert not root_build_file.exists()

        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)
        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        for _ in range(20):
            states = ps(service_directory, services=["leaf"])
            if states and states["leaf"] == "exited":
                break
        assert states["leaf"] == "exited"
        assert build_file.exists()
        assert root_build_file.exists()

        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None

        assert not build_file.exists()
        assert not root_build_file.exists()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        if up:
            up.wait(5)

        if build_file.exists():
            os.unlink(build_file)
        if root_build_file.exists():
            os.unlink(root_build_file)


def test_no_build_on_up(process_cleaner):
    service_directory = tests_directory / "projects" / "build"
    build_file = service_directory / "message_leaf.tmp"
    root_build_file = service_directory / "message_root.tmp"

    try:
        up: subprocess.Popen = None
        assert not build_file.exists()
        assert not root_build_file.exists()

        up = subprocess.Popen(
            ["composeit", "up", "--no-build"], cwd=service_directory, stdout=subprocess.PIPE
        )
        process_cleaner.append(up)
        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        for _ in range(20):
            states = ps(service_directory, services=["leaf"])
            if states and states["leaf"] == "exited":
                break
        assert states["leaf"] == "exited"
        assert not build_file.exists()
        assert not root_build_file.exists()

        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None

        assert not build_file.exists()
        assert not root_build_file.exists()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        if up:
            up.wait(5)

        if build_file.exists():
            os.unlink(build_file)
        if root_build_file.exists():
            os.unlink(root_build_file)


def test_clean_single(process_cleaner):
    service_directory = tests_directory / "projects" / "build"
    build_file = service_directory / "message_leaf.tmp"
    root_build_file = service_directory / "message_root.tmp"

    try:
        up: subprocess.Popen = None
        assert not build_file.exists()
        assert not root_build_file.exists()

        up = subprocess.Popen(["composeit", "up"], cwd=service_directory, stdout=subprocess.PIPE)
        process_cleaner.append(up)
        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        for _ in range(20):
            states = ps(service_directory, services=["leaf"])
            if states and states["leaf"] == "exited":
                break
        assert states["leaf"] == "exited"
        assert build_file.exists()
        assert root_build_file.exists()

        subprocess.call(["composeit", "down", "root"], cwd=service_directory)
        for _ in range(20):
            states = ps(service_directory, services=["root"])
            if states and states["root"] == "exited":
                break

        assert not build_file.exists()
        assert not root_build_file.exists()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        if up:
            up.wait(5)

        if build_file.exists():
            os.unlink(build_file)
        if root_build_file.exists():
            os.unlink(root_build_file)


def test_build_env(process_cleaner):
    service_directory = tests_directory / "projects" / "build_env"
    build_file = service_directory / "env_build.tmp"

    try:
        up: subprocess.Popen = None
        assert not build_file.exists()

        up = subprocess.Popen(
            ["composeit", "up", "--build-arg", "B2=yy"],
            cwd=service_directory,
            stdout=subprocess.PIPE,
        )
        process_cleaner.append(up)
        # Note: need to wait for it to start the server
        first_line = up.stdout.readline().decode()
        assert first_line.startswith("Server created")

        for _ in range(20):
            states = ps(service_directory, services=["env_build"])
            if states and states["env_build"] == "exited":
                break
        assert states["env_build"] == "exited"
        assert build_file.exists()

        assert build_file.read_text().strip() == "B1xxB2yyEND"

        subprocess.call(["composeit", "down"], cwd=service_directory)
        for _ in range(20):
            states = ps(service_directory, services=["env_build"])
            if not states:
                break

        assert not build_file.exists()
    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        if up:
            up.wait(5)

        if build_file.exists():
            os.unlink(build_file)
