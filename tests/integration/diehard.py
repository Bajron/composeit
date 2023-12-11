import signal
import sys
import os
import time
import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="diehard",
        description="An application that is hard to kill",
    )
    parser.add_argument("-t", "--time", default=15, type=int, help="Sleep time on signal")

    options = parser.parse_args()

    def signal_handler(signal, frame):
        print(f"Sleeping for {options.time} on signal {signal}")
        time.sleep(options.time)
        sys.exit(0)

    # TODO: is there a way to do this on Windows?
    signal.signal(signal.SIGINT, signal_handler)

    i = 0
    while True:
        for _ in range(10):
            print("...", end="", flush=True)
            time.sleep(0.1)
        print(f"{i:.>10}", flush=True)
        i += 1


if __name__ == "__main__":
    main()
