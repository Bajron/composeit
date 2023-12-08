import signal
import sys
import os
import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="echo",
        description="Prints arguments and lines from stdin on screen",
    )
    parser.add_argument(
        "-v", "--value", default=[], action="append", type=str, help="String value to print"
    )
    parser.add_argument(
        "-e",
        "--environment",
        default=[],
        action="append",
        type=str,
        help="Environemnt variable name to print",
    )
    options = parser.parse_args()

    def signal_handler(signal, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print("Values:", flush=True)
    for v in options.value:
        print(v, flush=True)
    print("Variables:", flush=True)
    for e in options.environment:
        print(f"{e}={os.environ.get(e)}", flush=True)

    print("Input:", flush=True)
    line = sys.stdin.readline()
    while line:
        print(line, end="", flush=True)
        line = sys.stdin.readline()


if __name__ == "__main__":
    main()
