import subprocess
import json


def test_version_short():
    out = subprocess.check_output(["composeit", "version", "--short"], universal_newlines=True)
    parts = out.split(".")
    assert len(parts) == 3
    assert all(int(p) >= 0 for p in parts)


def test_version():
    out = subprocess.check_output(["composeit", "version"], universal_newlines=True)
    lines = out.splitlines()
    assert len(lines) >= 3


def test_version_json():
    out = subprocess.check_output(
        ["composeit", "version", "--format", "json"], universal_newlines=True
    )
    j = json.loads(out)
    assert "composeit" in j


def test_version_json_short():
    out = subprocess.check_output(
        ["composeit", "version", "--short", "--format", "json"], universal_newlines=True
    )
    j = json.loads(out)
    assert "composeit" in j
    assert len(j) == 1
