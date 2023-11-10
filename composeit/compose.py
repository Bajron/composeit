import yaml
import argparse
import pathlib
import subprocess
import sys
import time


def start(file: pathlib.Path):
    f = yaml.safe_load(file.open())
    for s in f["services"]:
        print(s)
        print(f["services"][s])

    try:
        services = [
            subprocess.Popen(
                #executable=s["command"],
                args=([s["command"]] + s.get("args", [])),
                shell=s.get("shell", False),
                stderr=sys.stderr,
                stdout=sys.stdout,
            )
            for name, s in f["services"].items()
        ]
        time.sleep(3)
    finally:
        for s in services:
            s.terminate()


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Start services defined in file",
    )
    parser.add_argument("-f", "--file", type=pathlib.Path)

    options = parser.parse_args()
    print(options)

    start(options.file)


if __name__ == "main":
    main()
