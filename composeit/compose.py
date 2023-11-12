import yaml
import argparse
import pathlib
import subprocess
import asyncio
import signal
import os
import sys
import logging
from termcolor import colored
import termcolor

import aiohttp
from aiohttp import web, ClientConnectorError

USABLE_COLORS = list(termcolor.COLORS.keys())[2:-1]


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Define and run applications",
    )
    cfg_log = logging.getLogger("config")
    parser.add_argument("-f", "--file", default=None, type=pathlib.Path, help="Compose configuration file")
    parser.add_argument("-p", "--project-name", default=None, type=str, help="Project name")
    parser.add_argument("--project-directory", default=None, type=pathlib.Path, help="Alternate working directory")
    parser.add_argument("--env-file", default=[], action="extend", type=pathlib.Path)
    parser.add_argument("--verbose", default=False, action="store_true")

    parser.add_argument("--test-server", default=False, action="store_true")

    subparsers = parser.add_subparsers(help="sub-command help")
    parser_up = subparsers.add_parser("up", help="Startup the services")
    parser_up.add_argument("--build", default=False, action="store_true", help="Rebuild services")
    parser_up.add_argument("service", nargs="*", help="Specific service to start")

    parser_down = subparsers.add_parser("down", help="Close and cleanup the services")
    parser_down.add_argument("service", nargs="*", help="Specific service to close")

    options = parser.parse_args()
    print(options)

    if options.verbose:
        print(" ** Verbose **")
        logging.basicConfig(level=logging.DEBUG)

    working_directory = pathlib.Path(os.getcwd())
    if options.project_directory:
        working_directory = options.project_directory
    elif options.file:
        working_directory = options.file.parent
    working_directory = working_directory.absolute()
    cfg_log.debug(f"Working directory: {working_directory}")

    file_choices = ["composeit.yml", "composeit.yaml"]
    # TODO support multiple
    if options.file:
        service_file = options.file.absolute()
    else:
        for f in file_choices:
            if (working_directory / f).exists():
                service_file = working_directory / f
                break
        else:
            service_file = working_directory / file_choices[0]
    cfg_log.debug(f"Service file: {service_file}")

    project_name = options.project_name if options.project_name else working_directory.name

    cfg_log.debug(f"Project name: {project_name}")

    communication_pipe = get_comm_pipe(working_directory, project_name)
    cfg_log.debug(f"Communication pipe: {communication_pipe}")

    env_files = [e.absolute() for e in options.env_file]

    # NOTE: evaluate all paths from the commandline before changing working directory
    os.chdir(working_directory)
    cfg_log.debug(f"Changed directory to: {working_directory}")

    if len(env_files) == 0 and pathlib.Path(".env").exists():
        env_files = [pathlib.Path(".env").absolute()]
    cfg_log.debug(f"Environment files: {env_files}")

    for env_file in env_files:
        if env_file.exists():
            cfg_log.debug(f"Reading environment file {env_file}")
            env = read_env_file(env_file)
            os.environ.update(env)
        else:
            print("Provided environment file does not exist", file=sys.stderr)
            return 1

    if options.test_server:
        resolve_side_action(communication_pipe)
    else:
        start(service_file, communication_pipe)


def read_env_file(path: pathlib.Path):
    r = {}
    for line in path.open().readlines():
        line = line.split("#")[0].strip()
        if len(line) == 0:
            continue
        # TODO: should it be an error?
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        r[k] = v
    return r


def resolve_side_action(comm_pipe_path):
    asyncio.run(run_client_session(comm_pipe_path))


async def run_client_session(comm_pipe_path):
    try:
        print("connect", comm_pipe_path)
        if os.name == "nt":
            async with aiohttp.NamedPipeConnector(path=comm_pipe_path) as connector:
                await run_client_commands(connector)
        else:
            async with aiohttp.UnixConnector(path=comm_pipe_path) as connector:
                await run_client_commands(connector)
    except ClientConnectorError as ex:
        print(f" ** Connection error for {comm_pipe_path}: {ex}")
    except FileNotFoundError as ex:
        print(f" ** Name error for {comm_pipe_path}: {ex}")
    finally:
        pass


async def run_client_commands(connector):
    async with aiohttp.ClientSession(connector=connector) as session:
        response = await session.get("http://foo/")
        print("http client:", response)


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


def start(file: pathlib.Path, communication_pipe: str):
    # TODO: handle multiple files, merging checks
    parsed_data = yaml.load(file.open(), Loader=UniqueKeyLoader)
    for s in parsed_data["services"]:
        print(s)
        print(parsed_data["services"][s])

    service_name = file.parent.name

    asyncio.run(watch_services(file.parent, parsed_data, service_name, communication_pipe))


async def hello(request):
    return web.Response(text="Hello, world")


from aiohttp.web import cast, Application, AppRunner, AccessLogger, NamedPipeSite, UnixSite


async def run_server(app, path, delete_pipe=True):
    # Adapted from aiohttp.web._run_app
    try:
        print("Creating server", path)
        if asyncio.iscoroutine(app):
            app = await app  # type: ignore[misc]

        app = cast(Application, app)

        runner = AppRunner(
            app,
            handle_signals=True,
            access_log_class=AccessLogger,
            access_log_format=AccessLogger.LOG_FORMAT,
            access_log=logging.getLogger("httpserver"),
            keepalive_timeout=75,
        )
        await runner.setup()
        sites = []
        if os.name == "nt":
            sites.append(NamedPipeSite(runner=runner, path=f"{path}"))
        else:
            sites.append(UnixSite(runner=runner, path=f"{path}"))

        for site in sites:
            await site.start()
        delay = 10
        while True:
            await asyncio.sleep(delay)
    finally:
        await runner.cleanup()
        # Seems to be cleaned on Windows
        if delete_pipe and os.name != "nt":
            os.unlink(f"{path}")


def get_comm_pipe(directory_path, service_name=None):
    if service_name is None:
        service_name = directory_path.name

    if os.name == "nt":
        return r"\\.\pipe\composeit_" + f"{service_name}"

    return str(directory_path / ".compose")


async def watch_services(path, compose_config, service_name, communication_pipe, use_color=True):
    try:
        app = web.Application()
        app.add_routes([web.get("/", hello)])

        asyncio.get_event_loop().create_task(run_server(app, communication_pipe))

        if use_color and os.name == "nt":
            os.system("color")

        services = [
            AsyncProcess(i, name, service_config, use_color)
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


RestartPolicyOptions = ["no", "always", "on-failure", "unless-stopped"]


class AsyncProcess:
    def __init__(self, sequence: int, name: str, service_config: dict, use_color: bool):
        self.sequence = sequence
        self.name = name
        self.service_config = service_config
        self.process_initialization = AsyncProcess._make_process(self.service_config)
        self.process: asyncio.subprocess.Process = None
        self.use_color = use_color
        self.restart = service_config.get("restart", "no")
        assert self.restart in RestartPolicyOptions

        self.color = USABLE_COLORS[self.sequence % len(USABLE_COLORS)]

        self.rc = None
        self.terminated = False
        self.stopped = False
        self.exception = None

    async def _make_process(service_config):
        if service_config.get("inherit_environment", True):
            env = None
        else:
            # TODO: minimal viable env
            if os.name == "nt":
                env = {"SystemRoot": os.environ.get("SystemRoot", "")}
            else:
                env = {}

        # TODO env files

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

        if "working_dir" in service_config:
            popen_kw["cwd"] = service_config["working_dir"]

        passthrough = ["user", "group", "extra_groups", "umask"]
        for pt in passthrough:
            if pt in service_config:
                popen_kw[pt] = service_config[pt]

        if service_config.get("shell", False):
            # TODO, injections?
            if "args" in service_config:
                print(" ** args ignored with a shell command")
            process = await asyncio.create_subprocess_shell(
                cmd=service_config["command"],
                stderr=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                **popen_kw,
            )
        else:
            command = (
                service_config["command"]
                if isinstance(service_config["command"], list)
                else [service_config["command"]]
            )
            command.extend(service_config.get("args", []))
            process = await asyncio.create_subprocess_exec(
                *command, stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, **popen_kw
            )
        return process

    async def started(self):
        self.process = await self.process_initialization

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

    async def _resolve_restart(self):
        print(f"Resolving restart for {self.name}", self.terminated, self.stopped)
        if self.terminated:
            return False

        if self.restart == "always":
            await self._restart()
        elif self.restart == "unless-stopped" and not self.stopped:
            # TODO this would make more sense with side control and daemon mode
            await self._restart()
        elif self.restart == "on-failure" and self.rc != 0:
            await self._restart()
        else:
            return False
        return True

    async def watch(self):
        while True:
            try:
                await self.started()
                await asyncio.gather(self.wait_for_code(), self.watch_stderr(), self.watch_stdout())
                print(f" ** {self.service_config['command']} finished with error code {self.rc}")
                print(f" ** Restart policy is {self.restart}")
                if not await self._resolve_restart():
                    break
            except FileNotFoundError as ex:
                print(f" ** Error running command {self.name} {ex}")
                self.exception = ex
                break

    async def _restart(self):
        self.rc = None
        self.process = None
        self.process_initialization = AsyncProcess._make_process(self.service_config)

    def stop(self):
        self.stopped = True
        if self.rc is None and self.process is not None:
            self.process.terminate()

    def terminate(self):
        self.terminated = True
        if self.rc is None and self.process is not None:
            self.process.terminate()


if __name__ == "main":
    main()
