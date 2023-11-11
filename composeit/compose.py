import yaml
import argparse
import pathlib
import subprocess
import sys
import time
import io
import asyncio
import signal


# https://gist.github.com/pypt/94d747fe5180851196eb
class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = set()
        for key_node, value_node in node.value:
            if ":merge" in key_node.tag:
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise ValueError(f"Duplicate {key!r} key found in YAML.")
            mapping.add(key)
        return super().construct_mapping(node, deep)


class Process:
    def __init__(self, name, s):
        self.name = name
        self.definition = s
        self.process = subprocess.Popen(
            # executable=s["command"],
            args=([s["command"]] + s.get("args", [])),
            shell=s.get("shell", False),
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        self.rc = None
        self.stderr = io.TextIOWrapper(self.process.stderr, line_buffering=True)
        self.stdout = io.TextIOWrapper(self.process.stdout, line_buffering=True)

    def update(self):
        if self.rc is not None:
            return self.rc

        # This is blocking, put it in threads?
        for l in self.stderr.readlines():
            print(f"{self.name}> {l}", end="")
        for l in self.stdout.readlines():
            print(f"{self.name}: {l}", end="")

        self.rc = self.process.poll()
        if self.rc is not None:
            print(f" ** {self.definition['command']} finished with error code {self.rc}")
            return self.rc
        return self.rc

    def finished(self):
        return self.process.poll() is not None

    def terminate(self):
        return self.process.terminate()


def watch_services_with_threads(f):
    try:
        services = [Process(name, s) for name, s in f["services"].items()]
        while not all([s.update() is not None for s in services]):
            time.sleep(0.1)
    finally:
        for s in services:
            s.terminate()


def start(file: pathlib.Path, with_threads=False):
    # f = yaml.safe_load(file.open())
    f = yaml.load(file.open(), Loader=UniqueKeyLoader)
    for s in f["services"]:
        print(s)
        print(f["services"][s])

    if with_threads:
        watch_services_with_threads(f)
    else:
        asyncio.run(watch_services(f))


async def make_process(name, s):
    if s.get("shell", False):
        # TODO, injections?
        process = await asyncio.create_subprocess_shell(
            cmd=" ".join([s["command"]] + s.get("args", [])),
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *([s["command"]] + s.get("args", [])),
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
    return AsyncProcess(name, s, process)


class AsyncProcess:
    def __init__(self, name, s, process: asyncio.subprocess.Process):
        self.name = name
        self.definition = s
        self.process = process
        self.rc = None

    async def watch_stderr(self):
        async for l in self.process.stderr:
            print(f"{self.name}> {l.decode()}", end="")

    async def watch_stdout(self):
        async for l in self.process.stdout:
            print(f"{self.name}: {l.decode()}", end="")

    async def wait_for_code(self):
        self.rc = await self.process.wait()

    async def watch(self):
        await asyncio.gather(self.wait_for_code(), self.watch_stderr(), self.watch_stdout())
        print(f" ** {self.definition['command']} finished with error code {self.rc}")

    def terminate(self):
        if self.rc is None:
            self.process.terminate()


async def watch_services(f):
    try:
        services = [await make_process(name, s) for name, s in f["services"].items()]

        def signal_handler(signal, frame):
            print(" *** Calling terminate on sub processes")
            for s in services:
                s.terminate()

        signal.signal(signal.SIGINT, signal_handler)

        await asyncio.gather(*[s.watch() for s in services])
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
