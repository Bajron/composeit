import yaml
import argparse
import pathlib
import subprocess
import asyncio
import signal


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Start services defined in file",
    )
    parser.add_argument("-f", "--file", type=pathlib.Path)

    options = parser.parse_args()
    print(options)

    start(options.file)


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


def start(file: pathlib.Path, with_threads=False):
    # parsed_data = yaml.safe_load(file.open())
    parsed_data = yaml.load(file.open(), Loader=UniqueKeyLoader)
    for s in parsed_data["services"]:
        print(s)
        print(parsed_data["services"][s])

    asyncio.run(watch_services(parsed_data))


async def watch_services(f):
    try:
        services = [await make_process(name, s) for name, s in f["services"].items()]

        def shutdown_action():
            print(" *** Calling terminate on sub processes")
            for s in services:
                s.terminate()

        def signal_handler(signal, frame):
            asyncio.get_event_loop().call_soon_threadsafe(shutdown_action)

        signal.signal(signal.SIGINT, signal_handler)

        # Well, this is not implemented for Windows
        # asyncio.get_event_loop().add_signal_handler(signal.SIGINT, signal_handler)

        await asyncio.gather(*[s.watch() for s in services])
    finally:
        for s in services:
            s.terminate()


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


if __name__ == "main":
    main()
