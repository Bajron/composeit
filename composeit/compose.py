import yaml
import asyncio
import signal
import os
import logging
import termcolor
import aiohttp
import sys
import pathlib
import hashlib
import copy
import time
from aiohttp import web, ClientConnectorError, ClientResponseError
from json.decoder import JSONDecodeError

from typing import List, Dict, Optional

from .process import AsyncProcess
from .graph import topological_sequence
from .web_utils import ResponseAdapter, WebSocketAdapter
from .utils import resolve, duration_text, cumulative_time_text, date_time_text, get_stack_string
from socket import gethostname

USABLE_COLORS = list(termcolor.COLORS.keys())[2:-1]


class ColorAssigner:
    def __init__(self) -> None:
        self.sequence = 0

    def next(self):
        color = USABLE_COLORS[self.sequence % len(USABLE_COLORS)]
        self.sequence += 1
        return color


class Compose:
    def __init__(
        self,
        project_name: str,
        working_directory: pathlib.Path,
        service_files: List[pathlib.Path],
        verbose: bool = False,
        use_color: bool = True,
        defer_config_load: bool = False,
    ) -> None:
        self.project_name: str = project_name
        self.working_directory: pathlib.Path = working_directory

        self.verbose: bool = verbose
        self.logger: logging.Logger = logging.getLogger(project_name)
        if self.verbose:
            self.logger.setLevel(logging.DEBUG)
        else:
            # TODO: fix formatting
            self.logger.setLevel(logging.INFO)

        self.communication_pipe: str = get_comm_pipe(working_directory, project_name)
        self.logger.debug(f"Communication pipe: {self.communication_pipe}")

        self.use_colors: bool = use_color
        self.color_assigner: ColorAssigner = ColorAssigner()

        self.service_files: List[pathlib.Path] = service_files
        self.service_config: dict = {"services": []}

        self.depending: Optional[Dict[str, List[str]]] = None
        self.depends: Optional[Dict[str, List[str]]] = None

        if not defer_config_load:
            self.load_service_config()

        self.services: Dict[str, AsyncProcess] = {}
        self.app: Optional[Application] = None

    def load_service_config(self):
        self._parse_service_config()
        self._update_dependencies()

    def _get_next_color(self):
        return self.color_assigner.next() if self.use_colors else None

    def _parse_service_config(self):
        parsed_files = [
            yaml.load(file.open(), Loader=UniqueKeyLoader) for file in self.service_files
        ]
        # TODO validate format
        parsed_data = merge_configs(parsed_files)

        self.service_config = resolve(parsed_data)

    def _update_dependencies(self):
        services = self.service_config["services"]
        depending = {k: [] for k in services.keys()}
        depends = {k: [] for k in services.keys()}

        for name, service in services.items():
            if "depends_on" in service:
                depends_on = service["depends_on"]
                dependencies = depends_on if isinstance(depends_on, list) else [depends_on]
                depends[name] = dependencies
                for dep in dependencies:
                    if dep not in services:
                        raise Exception(f"Dependency {dep} not found")
                    depending[dep].append(name)

        self.depending = depending
        self.depends = depends

    def get_start_sequence(self, services: List[str]) -> List[str]:
        assert self.depends is not None
        return topological_sequence(services, self.depends)

    def get_stop_sequence(self, services: List[str]) -> List[str]:
        assert self.depending is not None
        return topological_sequence(services, self.depending)

    async def build(self, services=None):
        await self.watch_services(
            start_server=False, start_services=services, execute=False, execute_build=True
        )

    async def up(self, services=None):
        await self.start(services=services, execute_build=True)

    async def start(self, services=None, execute_build=False):
        server_up = await self.check_server_is_running()
        if server_up:
            await self.run_client_session(self.make_start_session(services, execute_build))
        else:
            await self.watch_services(start_services=services, execute_build=execute_build)

    def make_start_session(self, services=None, request_build=False):
        async def run_client_commands(session: aiohttp.ClientSession):
            response = await session.get(f"/")
            data = await response.json()
            project = data["project_name"]

            response = await session.get(f"/{project}")
            data = await response.json()
            existing_services = data["services"]

            if services is not None:
                to_request = []
                for service in services:
                    if service not in existing_services:
                        self.logger.warning(f"{service} does not exist on the server, skipping")
                        continue
                    to_request.append(service)
            else:
                to_request = existing_services

            for service in to_request:
                response = await session.post(
                    f"/{project}/{service}/start",
                    json={"request_build": request_build},
                )
                if response.status == 200:
                    print(service, await response.json())
            return response

        return run_client_commands

    async def down(self, services=None):
        server_up = await self.check_server_is_running()
        if server_up:
            # TODO: does not call clean until project stops, ok? not ok?
            #       down -> start may be a problem with the eager clean
            await self.stop(services=services, request_clean=True, server_up=server_up)
        else:
            await self.watch_services(
                services, start_server=False, execute=False, execute_build=False, execute_clean=True
            )

    async def stop(self, services=None, request_clean=False, server_up=None):
        if server_up is None:
            server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_stop_session(services, request_clean))
        else:
            self.logger.error("Server is not running")

    def get_always_restart_services(self):
        return [
            name for name, service in self.services.items() if service.restart_policy == "always"
        ]

    def make_stop_session(self, services=None, request_clean=False):
        async def run_client_commands(session: aiohttp.ClientSession):
            response = await session.get(f"/")
            data = await response.json()
            project = data["project_name"]

            response = await session.get(f"/{project}")
            data = await response.json()
            existing_services = data["services"]

            if services is not None:
                for service in services:
                    if service not in existing_services:
                        self.logger.warning(f"{service} does not exist on the server, skipping")
                        continue

                    self.logger.debug(f"Stopping /{project}/{service}")
                    response = await session.post(
                        f"/{project}/{service}/stop",
                        json={"request_clean": request_clean},
                    )
                    if response.status == 200:
                        print(service, await response.json())
            else:
                response = await session.post(
                    f"/{project}/stop", json={"request_clean": request_clean}
                )
                if response.status == 200:
                    print(project, await response.json())

            return response

        return run_client_commands

    async def logs(self, services=None):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_logs_session(services))
        else:
            self.logger.error("Server is not running")

    def make_logs_session(self, services=None):
        async def stream_logs_response(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                data = await response.json()
                project = data["project_name"]

            def logs_request():
                if services is not None and len(services) == 1:
                    service = services[0]
                    return session.get(f"/{project}/{service}/logs")
                else:
                    params = [("service", s) for s in services] if services else None
                    return session.get(f"/{project}/logs", params=params)

            async with logs_request() as response:
                close_requested = False

                def signal_handler(signal, frame):
                    nonlocal close_requested
                    close_requested = True
                    asyncio.get_event_loop().call_soon_threadsafe(response.close)

                signal.signal(signal.SIGINT, signal_handler)

                try:
                    self.logger.debug("Reading logs")
                    async for line in response.content:
                        print(line.decode(), end="")
                except asyncio.CancelledError:
                    self.logger.debug("Request cancelled")
                except aiohttp.ClientConnectionError as ex:
                    if not close_requested:
                        self.logger.warning("Connection error {ex}")

        return stream_logs_response

    async def attach(self, service):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_attach_session(service))
        else:
            self.logger.error("Server is not running")

    def make_attach_session(self, service):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                data = await response.json()
                project = data["project_name"]

            async with session.ws_connect(f"/{project}/{service}/attach") as ws:
                exit_requested = False

                def signal_handler(signal, frame):
                    nonlocal exit_requested
                    exit_requested = True
                    asyncio.get_event_loop().create_task(ws.close())

                signal.signal(signal.SIGINT, signal_handler)

                async def print_ws_strings():
                    while not ws.closed:
                        try:
                            line = await ws.receive_str()
                            print(line, end="")
                        except TypeError:
                            if not exit_requested:
                                self.logger.warning("Connection broken")
                            # TODO: how to break the sys.stdin.readline?
                            #       cannot really make it asyncio on Windows...
                            #       https://stackoverflow.com/questions/31510190/aysncio-cannot-read-stdin-on-windows
                            break

                loop = asyncio.get_running_loop()
                loop.create_task(print_ws_strings())

                try:
                    while not ws.closed:
                        input_line = await loop.run_in_executor(None, sys.stdin.readline)
                        if not ws.closed:
                            await ws.send_str(input_line)
                except asyncio.CancelledError:
                    self.logger.debug("Attach cancelled")

        return client_session

    async def ps(self, services: Optional[List[str]] = None):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_ps_session(services))
        else:
            self.logger.error("Server is not running")

    def make_ps_session(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                compose_data = await response.json()
                project = compose_data["project_name"]

            nonlocal services
            if services is None:
                async with session.get(f"/{project}") as response:
                    project_data = await response.json()
                    services = project_data["services"]

            service_data = []
            for s in services:
                async with session.get(f"/{project}/{s}") as response:
                    service_data.append(await response.json())

            service_data.sort(key=lambda x: x["sequence"])
            now = time.time()

            header = [
                ("name", "NAME", 20),
                ("command", "COMMAND", 30),
                ("created", "CREATED", 12),
                ("status", "STATUS", 18),
            ]
            fmt = ""
            for n, _, w in header:
                fmt += f"{{{n}:{w}}} "
            self.logger.debug(f"Format is '{fmt}'")

            info_rows = []
            for s in service_data:
                self.logger.debug(f"Service {s}")

                row = {}
                row["name"] = s["name"]

                cmd = " ".join(s["command"])
                if len(cmd) > 30:
                    cmd = cmd[:27] + "..."
                row["command"] = cmd

                since_build = (now - s["build_time"]) if s["build_time"] is not None else None
                row["created"] = (
                    f"{duration_text(since_build)} ago" if since_build is not None else "--"
                )

                state = s["state"]
                if state == "started":
                    since_start = (now - s["start_time"]) if s["start_time"] is not None else None
                    row["status"] = f"Up {duration_text(since_start)}"
                elif state == "stopped":
                    since_stop = (now - s["stop_time"]) if s["stop_time"] is not None else None
                    rc = s["return_code"]
                    row["status"] = (
                        f"exited ({rc}) {duration_text(since_stop)} ago"
                        if since_stop is not None
                        else "stopped"
                    )
                elif state == "terminated":
                    since_stop = (now - s["stop_time"]) if s["stop_time"] else None
                    row["status"] = f"terminated {duration_text(since_stop)} ago"
                else:
                    row["status"] = state

                info_rows.append(row)

            print(f"Project: {project}, in {compose_data['working_directory']}")
            print(fmt.format(**{k: v for k, v, _ in header}))
            for row in info_rows:
                print(fmt.format(**row))

        return client_session

    async def top(self, services: Optional[List[str]] = None):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_top_session(services))
        else:
            self.logger.error("Server is not running")

    def make_top_session(self, services: Optional[List[str]] = None):
        async def client_session(session):
            async with session.get(f"/") as response:
                compose_data = await response.json()
                project = compose_data["project_name"]

            nonlocal services
            if services is None:
                async with session.get(f"/{project}") as response:
                    project_data = await response.json()
                    services = project_data["services"]

            processes = []
            for s in services:
                async with session.get(f"/{project}/{s}/top") as response:
                    processes.extend(await response.json())

            header = [
                ("uid", "UID", 16),
                ("pid", "PID", 6),
                ("ppid", "PPID", 6),
                ("c", "C", 5),
                ("stime", "STIME", 10),
                ("tty", "TTY", 6),
                ("time", "TIME", 10),
                ("cmd", "CMD", 20),
            ]
            fmt = ""
            for n, _, w in header:
                fmt += f"{{{n}:{w}}} "
            self.logger.debug(f"Format is '{fmt}'")

            def make_row(p: dict):
                row = {}
                row["uid"] = p["username"]
                row["pid"] = p["pid"]
                row["ppid"] = p["ppid"]
                row["c"] = p["cpu_percent"]
                row["stime"] = date_time_text(p["create_time"])
                row["tty"] = p["terminal"]
                row["time"] = cumulative_time_text(p["cpu_times"]["user"])
                row["cmd"] = " ".join(p["cmdline"])
                return row

            info_rows = []
            for process_info in processes:
                info_rows.append(make_row(process_info))

            print(f"Project: {project}, in {compose_data['working_directory']}")
            print(fmt.format(**{k: v for k, v, _ in header}))
            for row in info_rows:
                print(fmt.format(**row))

        return client_session

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

    def _get_project(self, request: web.Request):
        project = request.match_info.get("project", "")
        if project != self.project_name:
            return web.Response(status=404)
        return project

    async def post_stop_project(self, request: web.Request):
        self._get_project(request)

        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        request_clean = body.get("request_clean", False)

        self.shutdown(request_clean=request_clean)
        return web.json_response("Stopped")

    def _get_service(self, request: web.Request):
        service = request.match_info.get("service", "")
        if service not in self.services.keys():
            raise web.HTTPNotFound(reason="Service not found")
        return service

    async def get_project(self, request: web.Request):
        self._get_project(request)
        return web.json_response(self.get_project_json())

    def get_service_json(self, service_name):
        s = self.services[service_name]
        return {
            "sequence": s.sequence,
            "name": s.name,
            "state": s.get_state(),
            "build_time": s.build_time,
            "start_time": s.start_time,
            "stop_time": s.stop_time,
            "pid": s.process.pid if s.process is not None else None,
            "command": s.command,
            "return_code": s.rc,
            "pobject": s.popen_kw,
            "config": s.service_config,
        }

    async def get_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        return web.json_response(self.get_service_json(service))

    async def get_service_processes(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        return web.json_response(self.services[service].get_processes_info())

    async def get_attach(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)

        # https://docs.aiohttp.org/en/stable/web_lowlevel.html
        response = web.WebSocketResponse()

        back_stream = WebSocketAdapter(response)
        logs_to_response = logging.StreamHandler(back_stream)
        self.services[service].attach_log_handler(logs_to_response)

        def detach_logger(fut):
            self.logger.debug(f"Detaching log sender from {service}")
            self.services[service].detach_log_handler(logs_to_response)

        await response.prepare(request)

        assert response.task is not None
        response.task.add_done_callback(detach_logger)

        try:
            while not response.closed and not self.services[service].terminated:
                try:
                    line = await response.receive_str()

                    process = self.services[service].process
                    if process is None or process.stdin is None:
                        self.logger.warning("Cannot forward the input to the process")
                        break
                    process.stdin.write(line.encode())
                except TypeError:
                    break
        finally:
            self.logger.debug("Closing attach request")
            try:
                await response.close()
            except ConnectionResetError:
                self.logger.debug("Attach connection broken")

        return response

    async def get_service_logs(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        # https://docs.aiohttp.org/en/stable/web_lowlevel.html
        response = web.StreamResponse()
        response.enable_chunked_encoding()

        process = self.services[service]
        back_stream = ResponseAdapter(response)
        logs_to_response = logging.StreamHandler(back_stream)
        process.attach_log_handler(logs_to_response)

        def detach_logger(fut):
            self.logger.debug("Detaching log sender from service")
            process.detach_log_handler(logs_to_response)

        await response.prepare(request)

        assert response.task is not None
        response.task.add_done_callback(detach_logger)

        try:
            # TODO: cleaner solution? websocket here?
            while not await back_stream.is_broken() and not process.terminated:
                await asyncio.sleep(1)
        finally:
            self.logger.debug("Closing logs request")
            try:
                await response.write_eof()
            except ConnectionResetError:
                self.logger.debug("Log connection broken")
        return response

    async def get_services_logs(self, request: web.Request):
        self._get_project(request)

        services = request.query.getall("service", None)
        if services is None:
            services = list(self.services.keys())

        response = web.StreamResponse()
        response.enable_chunked_encoding()

        back_stream = ResponseAdapter(response)
        logs_to_response = logging.StreamHandler(back_stream)
        # TODO: colors in formatter via name? otherwise separate stream handlers and formatters :/
        # TODO: definitely need to organize loggers better
        logs_to_response.setFormatter(logging.Formatter("%(name)s %(message)s"))
        # TODO: structure here and format in the client
        # TODO: provide some recent logs buffer
        for s in services:
            self.services[s].attach_log_handler(logs_to_response)

        def detach_loggers(fut):
            for s in services:
                self.logger.debug(f"Detaching log sender from {s}")
                self.services[s].detach_log_handler(logs_to_response)

        await response.prepare(request)
        assert response.task is not None
        response.task.add_done_callback(detach_loggers)

        try:
            # TODO: cleaner solution? websocket here?
            while not await back_stream.is_broken() and not all(
                [self.services[s].terminated for s in services]
            ):
                await asyncio.sleep(1)
        finally:
            self.logger.debug("Closing log request")
            try:
                await response.write_eof()
            except ConnectionResetError:
                self.logger.debug("Log connection broken")

        return response

    async def post_stop_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        request_clean = body.get("request_clean", False)
        for s in self.get_stop_sequence([service]):
            if request_clean:
                self.services[s].request_clean()
            await self.services[s].stop()
        return web.json_response("Stopped")

    async def post_start_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        request_build = body.get("request_build", False)
        for s in self.get_start_sequence([service]):
            if request_build:
                self.services[s].request_build()
            # Last one in the sequence should be `service`
            message = "Started" if self.services[s].start() else "Running"
        return web.json_response(message)

    def start_server(self):
        self.app = web.Application()
        self.app.add_routes(
            [
                web.get("/", self.get_call),
                web.get("/{project}", self.get_project),
                web.post("/{project}/stop", self.post_stop_project),
                web.get("/{project}/logs", self.get_services_logs),
                web.get("/{project}/{service}", self.get_service),
                web.get("/{project}/{service}/attach", self.get_attach),
                web.get("/{project}/{service}/logs", self.get_service_logs),
                web.post("/{project}/{service}/stop", self.post_stop_service),
                web.post("/{project}/{service}/start", self.post_start_service),
                web.get("/{project}/{service}/top", self.get_service_processes),
            ]
        )

        asyncio.get_event_loop().create_task(
            run_server(self.app, self.logger.getChild("http"), self.communication_pipe)
        )

    def shutdown(self, request_clean=False):
        self.logger.info(" *** Calling terminate on sub processes")
        for service in self.get_stop_sequence(self.services.keys()):
            if request_clean:
                self.services[service].request_clean()
            self.services[service].terminate()

    async def watch_services(
        self,
        start_services=None,
        start_server=True,
        execute=True,
        execute_build=False,
        execute_clean=False,
    ):
        try:
            if start_server:
                self.start_server()

            self.services = {
                name: AsyncProcess(
                    index,
                    name,
                    service_config,
                    self._get_next_color(),
                    execute=execute,
                    execute_build=execute_build,
                    execute_clean=execute_clean,
                )
                for (index, (name, service_config)) in enumerate(
                    self.service_config["services"].items()
                )
            }

            if self.verbose:
                for s in self.services.values():
                    s.log.setLevel(logging.DEBUG)

            def signal_handler(signal, frame):
                asyncio.get_event_loop().call_soon_threadsafe(self.shutdown)

            signal.signal(signal.SIGINT, signal_handler)
            # Well, this is not implemented for Windows
            # asyncio.get_event_loop().add_signal_handler(signal.SIGINT, signal_handler)

            if start_services is None:
                start_services = list(self.services.keys())
            else:
                additional = self.get_always_restart_services()
                if len(additional) > 0:
                    self.logger.debug(
                        f"Services with restart policy 'always' are started as well: {additional}"
                    )
                start_services += additional

            for name in self.get_start_sequence(start_services):
                self.services[name].start()

            self.logger.debug("Waiting for services")
            await asyncio.gather(*[s.watch() for s in self.services.values()])
            self.logger.debug("Services closed")
        except Exception as ex:
            self.logger.debug(get_stack_string())
            self.logger.debug(f"Exception during watching services: {ex}")
            self.shutdown()

    async def run_client_session(self, session_function):
        try:
            return await self.execute_client_session(session_function)
        except ClientConnectorError as ex:
            self.logger.error(f"Connection error for {self.communication_pipe}: {ex}")
        except FileNotFoundError as ex:
            self.logger.error(f"Name error for {self.communication_pipe}: {ex}")
        except ClientResponseError as ex:
            self.logger.error(f"Error response: {ex}")
        return None

    async def execute_with_connector(self, connector_function):
        self.logger.debug(f"Connect: {self.communication_pipe}")
        if os.name == "nt":
            async with aiohttp.NamedPipeConnector(path=self.communication_pipe) as connector:
                return await connector_function(connector)
        else:
            async with aiohttp.UnixConnector(path=self.communication_pipe) as connector:
                return await connector_function(connector)

    async def execute_client_session(self, session_function):
        async def with_connector(connector):
            hostname = gethostname()
            async with aiohttp.ClientSession(
                base_url=f"http://{hostname}",
                connector=connector,
                raise_for_status=True,
            ) as session:
                return await session_function(session)

        return await self.execute_with_connector(with_connector)

    async def check_server_is_running(self):
        try:
            data = await self.execute_client_session(self.make_server_check())
            if not data:
                return False

            if data["project_name"] != self.project_name:
                raise Exception(
                    f"Unexpected project received from the server. Received {data['project_name']} while expecting {self.project_name}"
                )

            return True
        except ClientConnectorError as ex:
            pass
        except FileNotFoundError as ex:
            pass
        return False

    def make_server_check(self):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/", timeout=10) as response:
                return await response.json()

        return client_session


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


from aiohttp.web import (
    cast,
    Application,
    AppRunner,
    AccessLogger,
    NamedPipeSite,
    UnixSite,
    BaseSite,
)


async def run_server(app: Application, logger: logging.Logger, path: str, delete_pipe=True):
    # Adapted from aiohttp.web._run_app
    try:
        if asyncio.iscoroutine(app):
            app = await app  # type: ignore[misc]

        app = cast(Application, app)

        runner = AppRunner(
            app,
            handle_signals=True,
            access_log_class=AccessLogger,
            access_log_format=AccessLogger.LOG_FORMAT,
            access_log=logger,
            keepalive_timeout=75,
        )
        await runner.setup()
        sites: List[BaseSite] = []
        if os.name == "nt":
            sites.append(NamedPipeSite(runner=runner, path=f"{path}"))
        else:
            sites.append(UnixSite(runner=runner, path=f"{path}"))

        for site in sites:
            await site.start()

        logger.debug("Server created")
        # Signal we create a server (might be unexpected)
        print("Server created", path, flush=True)

        delay = 10
        while True:
            await asyncio.sleep(delay)
    finally:
        logger.debug("Cleanup after run_server")
        await runner.cleanup()
        # Seems to be cleaned on Windows
        if delete_pipe and os.name != "nt":
            os.unlink(f"{path}")


def get_comm_pipe(directory_path: pathlib.Path, project_name: Optional[str] = None):
    if project_name is None:
        project_name = directory_path.name

    if os.name == "nt":
        h = hashlib.sha256(str(directory_path.resolve()).encode()).hexdigest()
        return r"\\.\pipe\composeit_" + f"{project_name}_{h}"

    return str(directory_path / f".{project_name}.daemon")


def merge_configs(parsed_files):
    base = copy.deepcopy(parsed_files[0])
    for i in range(1, len(parsed_files)):
        base = merge_in(base, parsed_files[i])
    return base


def merge_in(base, incoming):
    # TODO any other top level keys?
    base_svcs = base["services"]
    for name, service in incoming["services"].items():
        if name not in base_svcs:
            base_svcs[name] = service
        else:
            base_svcs[name] = merge_services(base_svcs[name], service)
    return base


def merge_command_sequence(base, incoming):
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
        else:
            base_value = base[key]
            if isinstance(value, list):
                base_value += value
            else:
                base_value += [value]
    return base


def merge_services(base, incoming):
    for key, value in incoming.items():
        if key not in base:
            base[key] = value
        else:
            base_value = base[key]
            if key in ["command"]:  # TODO: which commands not to merge?
                base[key] = value
            elif key == ["build", "clean"]:
                base[key] = merge_command_sequence(base[key], incoming[key])
            elif isinstance(base_value, list):
                if isinstance(value, list):
                    base_value += value
                elif isinstance(value, dict):
                    print("TODO list vs dict")  # env case? TODO: normalize env to something?
                else:
                    base_balue += [value]
            elif isinstance(base_value, dict):
                if isinstance(value, dict):
                    base_value.update(value)
                elif isinstance(value, list):
                    print("TODO dict vs list")  # env case? TODO: normalize env to something?
                else:
                    # TODO just throw? this is weird
                    print("dict vs value")
                    base_value[value] = None
            else:
                base[key] = value
    return base
