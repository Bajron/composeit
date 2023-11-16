import yaml
import asyncio
import signal
import os
import logging
import termcolor
import dotenv
import aiohttp
import io
from aiohttp import web, ClientConnectorError
from .process import AsyncProcess


USABLE_COLORS = list(termcolor.COLORS.keys())[2:-1]


def resolve_string(s: str):
    # NOTE: This is not fully compatible with https://docs.docker.com/compose/environment-variables/env-file/
    #       Required and alternative values are not handled. Single quote also a little different.
    #       We are working on that though B]
    #       https://github.com/Bajron/python-dotenv
    return dotenv.dotenv_values(io.StringIO(f'X="{s}"'))["X"]
    return dotenv.main.resolve_variables({"H": f"{s}"})["H"]
    # TODO: single quotes
    # TODO: new interface after change


class ColorAssigner:
    def __init__(self) -> None:
        self.sequence = 0

    def next(self):
        color = USABLE_COLORS[self.sequence % len(USABLE_COLORS)]
        self.sequence += 1
        return color


class Compose:
    def __init__(self, project_name, working_directory, service_files, use_color=True) -> None:
        self.project_name = project_name
        self.working_directory = working_directory

        self.logger = logging.getLogger(project_name)

        self.communication_pipe = get_comm_pipe(working_directory, project_name)
        self.logger.debug(f"Communication pipe: {self.communication_pipe}")

        self.use_colors = use_color
        self.color_assigner = ColorAssigner()

        self.service_files = service_files
        self.service_config = {}
        self.services = {}
        self.app = None

    def _get_next_color(self):
        return self.color_assigner.next() if self.use_colors else None

    def run(self):
        # TODO: handle multiple files, merging checks
        for file in self.service_files:
            parsed_data = yaml.load(file.open(), Loader=UniqueKeyLoader)
            for s in parsed_data["services"]:
                print(s)
                print(parsed_data["services"][s])
            break  # FIXME

        self.service_config = parsed_data

        asyncio.run(self.watch_services(self.use_colors))

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
        return {"name": s.name, "pid": s.process.pid, "pobject": s.popen_kw}

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
                name: AsyncProcess(i, name, service_config, self._get_next_color())
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
