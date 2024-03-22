import subprocess

from .utils import *


def test_up_default_only(process_cleaner):
    service_directory = tests_directory / "projects" / "profiles"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory)
        process_cleaner.append(up)
        wait_for_ps(service_directory)

        states = ps(service_directory)
        assert states["simple1"] == "up"
        assert states["simple2a"] == "stopped"
        assert states["simple2aa"] == "stopped"
        assert states["simple2b"] == "stopped"

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_up_profile(process_cleaner):
    service_directory = tests_directory / "projects" / "profiles"

    try:
        up = subprocess.Popen(["composeit", "--profile", "a", "up"], cwd=service_directory)
        process_cleaner.append(up)
        wait_for_ps(service_directory)

        states = ps(service_directory)
        assert states["simple1"] == "up"
        assert states["simple2a"] == "up"
        assert states["simple2aa"] == "up"
        assert states["simple2b"] == "stopped"

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_up_profiles(process_cleaner):
    service_directory = tests_directory / "projects" / "profiles"

    try:
        up = subprocess.Popen(
            ["composeit", "--profile", "a", "--profile", "b", "up"],
            cwd=service_directory,
            stdout=subprocess.PIPE,
        )
        process_cleaner.append(up)
        wait_for_ps(service_directory)

        states = ps(service_directory)
        assert states["simple1"] == "up"
        assert states["simple2a"] == "up"
        assert states["simple2aa"] == "up"
        assert states["simple2b"] == "up"

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None


def test_up_default_only_then_profile(process_cleaner):
    service_directory = tests_directory / "projects" / "profiles"

    try:
        up = subprocess.Popen(["composeit", "up"], cwd=service_directory)
        process_cleaner.append(up)
        wait_for_ps(service_directory)

        states = ps(service_directory)
        assert states["simple1"] == "up"
        assert states["simple2a"] == "stopped"
        assert states["simple2aa"] == "stopped"
        assert states["simple2b"] == "stopped"

        subprocess.run(["composeit", "--profile", "b", "start"], cwd=service_directory)
        ps_wait_for(service_directory, service="simple2b", state="up")
        states = ps(service_directory)
        assert states["simple1"] == "up"
        assert states["simple2a"] == "stopped"
        assert states["simple2aa"] == "stopped"
        assert states["simple2b"] == "up"

    finally:
        subprocess.call(["composeit", "down"], cwd=service_directory)
        rc = up.wait(5)
        assert rc is not None
