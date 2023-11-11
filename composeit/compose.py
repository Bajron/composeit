import yaml
import argparse
import pathlib
import subprocess
import asyncio
import signal
import os
from termcolor import colored
import termcolor

USABLE_COLORS = list(termcolor.COLORS.keys())[2:-1]


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


def start(file: pathlib.Path):
    # parsed_data = yaml.safe_load(file.open())
    parsed_data = yaml.load(file.open(), Loader=UniqueKeyLoader)
    for s in parsed_data["services"]:
        print(s)
        print(parsed_data["services"][s])

    asyncio.run(watch_services(parsed_data))


async def watch_services(compose_config, use_color=True):
    try:
        if use_color and os.name == "nt":
            os.system("color")

        services = [
            await make_process(i, name, service_config, use_color)
            for (i, (name, service_config)) in enumerate(compose_config["services"].items())
        ]

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


async def make_process(sequence, name, service_config, use_color):
    if service_config.get("inherit_environment", True):
        env = None
    else:
        # TODO: minimal viable env
        if os.name == "nt":
            env = {"SystemRoot": os.environ.get("SystemRoot", "")}
        else:
            env = {}

    if "environment" in service_config:
        if env is None:
            env = os.environ.copy()
        env_definition = service_config["environment"]
        if isinstance(env_definition, list):
            splits = [e.split("=", 1) for e in env_definition]
            to_add = {s[0]: s[1] if len(s) == 2 else os.environ.get(s[0], "") for s in splits}
        if isinstance(env_definition, dict):
            to_add = env_definition
        env.update(to_add)

    popen_kw = {"env": env}

    if service_config.get("shell", False):
        # TODO, injections?
        if "args" in service_config:
            print(" ** args ignored with a shell command")
        process = await asyncio.create_subprocess_shell(
            cmd=service_config["command"], stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, **popen_kw
        )
    else:
        command = (
            service_config["command"] if isinstance(service_config["command"], list) else [service_config["command"]]
        )
        command.extend(service_config.get("args", []))
        process = await asyncio.create_subprocess_exec(
            *command, stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, **popen_kw
        )
    return AsyncProcess(sequence, name, service_config, process, use_color)


class AsyncProcess:
    def __init__(
        self, sequence: int, name: str, service_config: dict, process: asyncio.subprocess.Process, use_color: bool
    ):
        self.sequence = sequence
        self.name = name
        self.definition = service_config
        self.process = process
        self.use_color = use_color

        self.color = USABLE_COLORS[self.sequence % len(USABLE_COLORS)]

        self.rc = None

    def _output(self, sep: str, message: str):
        s = f"{self.name}{sep} {message}"
        if self.use_color:
            s = colored(s, self.color)
        print(s, end="")

    async def watch_stderr(self):
        async for l in self.process.stderr:
            self._output(">", l.decode())

    async def watch_stdout(self):
        async for l in self.process.stdout:
            self._output(":", l.decode())

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
