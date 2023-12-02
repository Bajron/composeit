import signal
import time


def main():
    run = True

    def signal_handler(signal, frame):
        nonlocal run
        run = False

    signal.signal(signal.SIGINT, signal_handler)
    counter = 0
    while run:
        print(counter, flush=True)
        time.sleep(0.1)
        counter += 1


if __name__ == "__main__":
    main()
