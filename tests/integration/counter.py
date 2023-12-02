import signal
import time
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="counter",
        description="Print integer sequence on the screen",
    )
    parser.add_argument("-s", "--start", default=0, type=int, help="First element of the sequence")
    parser.add_argument("-d", "--delta", default=1, type=int, help="Increment of the sequence")
    parser.add_argument("-t", "--time", default=1.0, type=float, help="Sleep time between output")
    parser.add_argument("-e", "--exit", default=None, type=int, help="Value after which to exit")
    parser.add_argument("-r", "--return-code", default=0, type=int, help="Application return code")
    options = parser.parse_args()

    run = True

    def signal_handler(signal, frame):
        nonlocal run
        run = False

    signal.signal(signal.SIGINT, signal_handler)
    counter = options.start
    while run:
        print(counter, flush=True)
        time.sleep(options.time)
        counter += options.delta
        if options.exit is not None and counter > options.exit:
            run = False
    return options.return_code


if __name__ == "__main__":
    sys.exit(main())
