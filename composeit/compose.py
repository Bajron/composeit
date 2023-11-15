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
import dotenv
import aiohttp
import io
from aiohttp import web, ClientConnectorError

USABLE_COLORS = list(termcolor.COLORS.keys())[2:-1]


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Define and run applications",
    )
    cfg_log = logging.getLogger("config")
    parser.add_argument(
        "-f", "--file", nargs="*", default=[], action="extend", type=pathlib.Path, help="Compose configuration file"
    )
    parser.add_argument("-p", "--project-name", default=None, type=str, help="Project name")
    parser.add_argument("--project-directory", default=None, type=pathlib.Path, help="Alternate working directory")
    parser.add_argument("--env-file", default=[], action="extend", type=pathlib.Path)
    parser.add_argument("--verbose", default=False, action="store_true")

    parser.add_argument(
        "--test-server", default=None, help="Temporary option to perform GET with a provided URL on the server"
    )

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
    elif len(options.file) > 0:
        working_directory = options.file[0].parent
    working_directory = working_directory.absolute()
    cfg_log.debug(f"Working directory: {working_directory}")

    file_choices = ["composeit.yml", "composeit.yaml"]
    # TODO support multiple
    if len(options.file) > 0:
        service_files = [f.absolute() for f in options.file]
    else:
        for f in file_choices:
            if (working_directory / f).exists():
                service_files = [working_directory / f]
                break
        else:
            service_files = [working_directory / file_choices[0]]
    cfg_log.debug(f"Service file: {service_files}")

    project_name = options.project_name if options.project_name else working_directory.name

    cfg_log.debug(f"Project name: {project_name}")

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
            dotenv.load_dotenv(env_file, override=True)
        else:
            print("Provided environment file does not exist", file=sys.stderr)
            return 1

    compose = Compose(project_name, working_directory, service_files)

    if options.test_server is not None:
        compose.side_action(options.test_server)
    else:
        compose.run()


def resolve_string(s: str):
    # NOTE: This is not fully compatible with https://docs.docker.com/compose/environment-variables/env-file/
    #       Required and alternative values are not handled. Single quote also a little different.
    #       We are working on that though B]
    #       https://github.com/Bajron/python-dotenv
    return dotenv.dotenv_values(io.StringIO(f'X="{s}"'))["X"]
    return dotenv.main.resolve_variables({"H": f"{s}"})["H"]
    # TODO: single quotes
    # TODO: new interface after change


class Compose:
    def __init__(self, project_name, working_directory, service_files) -> None:
        self.project_name = project_name
        self.working_directory = working_directory

        self.logger = logging.getLogger(project_name)

        self.communication_pipe = get_comm_pipe(working_directory, project_name)
        self.logger.debug(f"Communication pipe: {self.communication_pipe}")

        self.service_files = service_files
        self.service_config = {}
        self.services = {}
        self.app = None

    def run(self):
        # TODO: handle multiple files, merging checks
        for file in self.service_files:
            parsed_data = yaml.load(file.open(), Loader=UniqueKeyLoader)
            for s in parsed_data["services"]:
                print(s)
                print(parsed_data["services"][s])
            break  # FIXME

        self.service_config = parsed_data

        asyncio.run(self.watch_services())

    def get_call_json(self):
        return {
            "project_name": self.project_name,
            "working_directory": str(self.working_directory),
            "service_files": [str(f) for f in self.service_files],
        }

    async def get_call(self, request):
        return web.json_response(self.get_call_json())

    def get_project_json(self):
        return {"services": [k for k in self.services.keys()]}

    async def get_project(self, request: web.Request):
        project = request.match_info.get("project", "")
        if project != self.project_name:
            return web.Response(status=404)

        return web.json_response(self.get_project_json())

    def get_service_json(self, service_name):
        s = self.services[service_name]
        return {"name": s.name, "pid": s.process.pid}

    async def get_service(self, request: web.Request):
        project = request.match_info.get("project", "")
        if project != self.project_name:
            return web.Response(status=404)

        service = request.match_info.get("service", "")
        if service not in self.services.keys():
            return web.Response(status=404)

        return web.json_response(self.get_service_json(service))

    def start_server(self):
        app = web.Application()
        app.add_routes(
            [
                web.get("/", self.get_call),
                web.get("/{project}", self.get_project),
                web.get("/{project}/{service}", self.get_service),
            ]
        )

        asyncio.get_event_loop().create_task(run_server(app, self.communication_pipe))

    async def watch_services(self, use_color=True):
        try:
            self.start_server()

            if use_color and os.name == "nt":
                os.system("color")

            self.services = {
                name: AsyncProcess(i, name, service_config, use_color)
                for (i, (name, service_config)) in enumerate(self.service_config["services"].items())
            }
            services = list(self.services.values())

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

    def side_action(self, action):
        asyncio.run(run_client_session(self.communication_pipe, action))


async def run_client_session(comm_pipe_path, url):
    try:
        print("connect", comm_pipe_path)
        if os.name == "nt":
            async with aiohttp.NamedPipeConnector(path=comm_pipe_path) as connector:
                await run_client_commands(connector, url)
        else:
            async with aiohttp.UnixConnector(path=comm_pipe_path) as connector:
                await run_client_commands(connector, url)
    except ClientConnectorError as ex:
        print(f" ** Connection error for {comm_pipe_path}: {ex}")
    except FileNotFoundError as ex:
        print(f" ** Name error for {comm_pipe_path}: {ex}")
    finally:
        pass


async def run_client_commands(connector, url):
    async with aiohttp.ClientSession(connector=connector) as session:
        response = await session.get(f"http://foo{url}")
        print("http client:", response)
        print(await response.json())


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


from aiohttp.web import cast, Application, AppRunner, AccessLogger, NamedPipeSite, UnixSite


async def run_server(app: Application, path: str, delete_pipe=True):
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


def get_comm_pipe(directory_path, project_name=None):
    if project_name is None:
        project_name = directory_path.name

    if os.name == "nt":
        return r"\\.\pipe\composeit_" + f"{project_name}"

    return str(directory_path / ".compose")


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

        self.popen_kw = {}

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

        if "env_file" in service_config or "environment" in service_config:
            if env is None:
                env = os.environ.copy()

        if "env_file" in service_config:
            ef = service_config["env_file"]
            if not isinstance(ef, list):
                ef = [ef]
            for f in ef:
                env.update(dotenv.dotenv_values(f))

        if "environment" in service_config:
            env_definition = service_config["environment"]
            if isinstance(env_definition, str):
                env_definition = [env_definition]
            if isinstance(env_definition, list):
                entries = [dotenv.dotenv_values(stream=io.StringIO(e)) for e in env_definition]
                to_add = {k: v if v is not None else os.environ.get(k, "") for d in entries for k, v in d.items()}
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
