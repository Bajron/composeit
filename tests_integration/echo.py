import signal
import sys


def main():
    def signal_handler(signal, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    while True:
        print(sys.stdin.readline(), end="", flush=True)


if __name__ == "__main__":
    main()
