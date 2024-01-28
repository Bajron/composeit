import subprocess
import json
from .utils import *


def test_config_commands():
    try:
        service_directory = tests_directory / "projects" / "config"

        def run_for_output(*commands: str):
            return subprocess.check_output(
                commands,
                cwd=service_directory,
                encoding="utf8",
            )

        default = run_for_output("composeit", "config")
        assert "second" in default
        assert "first" in default
        assert "${APP" not in default

        to_file = run_for_output("composeit", "config", "-o", "tmp.log")
        assert to_file.strip() == ""
        produced_output = service_directory / "tmp.log"
        assert produced_output.exists()
        file_txt = produced_output.read_text()
        assert "second" in file_txt
        assert "first" in file_txt
        assert "${APP" not in file_txt

        first_only = run_for_output("composeit", "config", "first")
        assert "second" not in first_only
        assert "first" in first_only

        quiet = run_for_output("composeit", "config", "-q")
        assert "second" not in quiet
        assert "first" not in quiet
        assert quiet.strip() == ""

        not_interpolated = run_for_output("composeit", "config", "--no-interpolate")
        assert "${APP" in not_interpolated

        json_output = run_for_output("composeit", "config", "--format", "json")
        as_json = json.loads(json_output)
        assert "services" in as_json and "second" in as_json["services"].keys()

        services_text = run_for_output("composeit", "config", "--services")
        services = [s.strip() for s in services_text.splitlines() if s.strip()]
        assert services == ["first", "second"]
    finally:
        if produced_output.exists():
            os.unlink(produced_output)
