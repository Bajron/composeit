import json
import asyncio
import datetime
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
from .service_config import get_signal, get_default_kill
from .log_utils import ComposeFormatter, build_format
from typing import List, Dict, Optional, Union, Iterable, Mapping, Set

from .log_utils import (
    JsonFormatter,
    build_print_function,
    print_message,
)
from .service_config import (
    ServiceFiles,
    get_shared_logging_config,
    get_command,
    get_process_path,
    resolve_command,
)
from .process import AsyncProcess
from .graph import topological_sequence
from .web_utils import ResponseAdapter, WebSocketAdapter
from .utils import (
    make_int,
    make_date,
    interpolate_variables,
    duration_text,
    cumulative_time_text,
    date_time_text,
    get_stack_string,
)
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
        service_files: ServiceFiles,
        profiles: Optional[List[str]],
        verbose: bool = False,
        use_color: bool = True,
        defer_config_load: bool = False,
        build_args: Optional[Dict[str, str]] = None,
        timeout: float = 10,
    ) -> None:
        self.project_name: str = project_name
        self.working_directory: pathlib.Path = working_directory
        self.create_time = time.time()

        self.verbose: bool = verbose
        self.logger: logging.Logger = logging.getLogger(project_name)
        if self.verbose:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

        self.communication_pipe: str = get_comm_pipe(working_directory, project_name)
        self.logger.debug(f"Communication pipe: {self.communication_pipe}")

        self.use_colors: bool = use_color
        self.color_assigner: ColorAssigner = ColorAssigner()

        self.service_files: ServiceFiles = service_files
        self.service_config: Optional[dict] = None

        self.profiles: List[str] = profiles or []

        self.depending: Optional[Mapping[str, Iterable[str]]] = None
        self.depends: Optional[Mapping[str, Iterable[str]]] = None

        self.shutdown_timeout: float = timeout

        if not defer_config_load:
            self.load_service_config()

        self.build_args: Dict[str, str] = build_args or {}

        self.services: Dict[str, AsyncProcess] = {}
        self.app: Optional[Application] = None

    def load_service_config(self, **load_options):
        self._parse_service_config(**load_options)
        self._update_dependencies()

    def assure_service_config(self, **load_options):
        if self.service_config is None:
            self.load_service_config(**load_options)

    def get_services_configs(self) -> Dict[str, dict]:
        self.assure_service_config()
        assert self.service_config is not None
        return self.service_config["services"] or {}

    def get_defaults_services(self, with_profiles: Optional[List[str]] = None) -> Iterable[str]:
        profiles = with_profiles if with_profiles is not None else self.profiles
        return [
            key
            for key, service in self.get_services_configs().items()
            if "profile" not in service or service["profile"] in profiles
        ]

    def _get_next_color(self):
        return self.color_assigner.next() if self.use_colors else None

    def _parse_service_config(
        self, check_consistency=True, interpolate=True, normalize=True, resolve_paths=True
    ):
        parsed_data = merge_configs(self.service_files.get_parsed_files())
        # TODO validate format

        self.service_config = parsed_data

        if check_consistency:
            # TODO check consistency
            pass

        if interpolate:
            self.service_config = interpolate_variables(self.service_config)

        if normalize:
            # TODO: normalize format (might be good for env for example)
            pass

        if resolve_paths:
            # TODO: resolve paths, (surely this could help with relative paths behavior)
            pass

    def _update_dependencies(self):
        assert self.service_config is not None and "services" in self.service_config

        services = self.get_services_configs()

        depending: Dict[str, List[str]] = {k: [] for k in services.keys()}
        depends: Dict[str, List[str]] = {k: [] for k in services.keys()}

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

    def get_start_sequence(self, services: Iterable[str]) -> List[str]:
        assert self.depends is not None
        return topological_sequence(services, self.depends)

    def get_stop_sequence(self, services: Iterable[str]) -> List[str]:
        assert self.depending is not None
        return topological_sequence(services, self.depending)

    async def build(self, services=None):
        return await self.watch_services(
            start_server=False, services=services, execute=False, execute_build=True
        )

    async def up(self, services=None, no_build=False, **kwargs):
        return await self.start(services=services, execute_build=not no_build, **kwargs)

    async def start(
        self,
        services=None,
        execute_build=False,
        abort_on_exit=False,
        code_from=None,
        attach=None,
        attach_dependencies=False,
        no_attach=None,
        no_deps=False,
        no_log_prefix=False,
        no_color=False,
        timestamps=False,
    ):
        server_up = await self.check_server_is_running()
        if server_up:
            return await self.run_client_session(
                self.make_start_session(services, execute_build, no_deps)
            )
        else:
            return await self.watch_services(
                services=services,
                execute_build=execute_build,
                abort_on_exit=abort_on_exit,
                code_from=code_from,
                attach=attach,
                attach_dependencies=attach_dependencies,
                no_attach=no_attach,
                no_deps=no_deps,
                no_log_prefix=no_log_prefix,
                no_color=no_color,
                timestamps=timestamps,
            )

    def make_start_session(self, services=None, request_build=False, no_deps=False):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = ComposeProxy(session, self)
            project = await remote.get_project()
            data = await remote.get_project_data()
            existing_services = data["services"]

            if services is not None:
                to_request = []
                for service in services:
                    if service not in existing_services:
                        self.logger.warning(f"{service} does not exist on the server, skipping")
                        continue
                    to_request.append(service)
            else:
                to_request = await remote.get_default_services()

            bad_responses = 0
            for service in to_request:
                response = await session.post(
                    f"/{project}/{service}/start",
                    json={"request_build": request_build, "no_deps": no_deps},
                )
                if response.status == 200:
                    print(service, await response.json())
                else:
                    bad_responses += 1

            return 0 if bad_responses == 0 else 1

        return run_client_commands

    async def down(self, services=None, timeout=None):
        server_up = await self.check_server_is_running()
        if server_up:
            return await self.stop(
                services=services, request_clean=True, server_up=server_up, timeout=timeout
            )
        else:
            return await self.watch_services(
                services, start_server=False, execute=False, execute_build=False, execute_clean=True
            )

    async def stop(self, services=None, request_clean=False, server_up=None, timeout=None):
        if server_up is None:
            server_up = await self.check_server_is_running()

        if server_up:
            return await self.run_client_session(
                self.make_stop_session(services, request_clean, timeout)
            )
        else:
            self.logger.error("Server is not running")
            return 1

    def make_stop_session(self, services=None, request_clean=False, timeout=None):
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
                    request = lambda: session.post(
                        f"/{project}/{service}/stop",
                        json={"request_clean": request_clean},
                    )

                    try:
                        response = await asyncio.wait_for(
                            request(),
                            timeout=timeout,
                        )
                    except asyncio.exceptions.TimeoutError:
                        self.logger.warning(f"Stopping {service} timed out after {timeout}")
                        response = await request()

                    if response.status == 200:
                        print(service, await response.json())
            else:
                request = lambda: session.post(
                    f"/{project}/stop", json={"request_clean": request_clean}
                )

                try:
                    response = await asyncio.wait_for(request(), timeout=timeout)
                except asyncio.exceptions.TimeoutError:
                    self.logger.warning(f"Stopping {project} timed out after {timeout}")
                    response = await request()

                if response.status == 200:
                    print(project, await response.json())

            return 0 if 200 <= response.status < 300 else 1

        return run_client_commands

    async def restart(self, services=None, **kwargs):
        server_up = await self.check_server_is_running()
        if server_up:
            return await self.run_client_session(self.make_restart_session(services, **kwargs))
        else:
            self.logger.error("Server is not running")
            return 1

    def make_restart_session(self, services=None, no_deps=False, timeout=None):
        async def run_client_commands(session: aiohttp.ClientSession):
            project = await get_project(session)

            specification = {"no_deps": no_deps}
            if timeout is not None:
                specification["timeout"] = timeout
            if services is not None:
                specification["services"] = services

            if services is not None and len(services) == 1:
                service = services[0]
                response = await session.post(
                    f"/{project}/{service}/restart",
                    json=specification,
                )
                if response.status == 200:
                    print(service, await response.json())
            else:
                response = await session.post(f"/{project}/restart", json=specification)
                if response.status == 200:
                    print(project, await response.json())

            return 0 if 200 <= response.status < 300 else 1

        return run_client_commands

    async def kill(self, services=None, signal=9):
        server_up = await self.check_server_is_running()
        if server_up:
            return await self.run_client_session(self.make_kill_session(services, signal))
        else:
            self.logger.error("Server is not running")
            return 1

    def make_kill_session(self, services=None, signal=9):
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

                    self.logger.debug(f"Kill {signal} to /{project}/{service}")
                    response = await session.post(
                        f"/{project}/{service}/kill",
                        json={"signal": signal},
                        raise_for_status=False,
                    )
            else:
                self.logger.debug(f"Kill {signal} to /{project}")
                response = await session.post(
                    f"/{project}/kill",
                    json={"signal": signal},
                    raise_for_status=False,
                )

            if response.status >= 500:
                response.raise_for_status()

            print(project, await response.json())

            return 0 if 200 <= response.status < 300 else 1

        return run_client_commands

    async def logs(
        self,
        services: Optional[List[str]] = None,
        **kwargs,
    ):
        server_up = await self.check_server_is_running()

        if server_up:
            return await self.run_client_session(self.make_logs_session(services, **kwargs))
        else:
            self.logger.error("Server is not running")
            return 1

    def make_logs_session(
        self,
        services: Optional[List[str]] = None,
        follow: bool = False,
        context: Optional[bool] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        tail: Optional[int] = None,
        color: Optional[bool] = None,
        timestamps: Optional[bool] = None,
        prefix: Optional[bool] = None,
    ):
        if context is None:
            context = not follow

        async def stream_logs_response(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                data = await response.json()
                project = data["project_name"]

            def logs_request():
                params = [
                    ("format", "json"),
                    ("context", "on" if context else "no"),
                    ("follow", "on" if follow else "no"),
                ]
                if since is not None:
                    params.append(("since", since.isoformat()))
                if until is not None:
                    params.append(("until", until.isoformat()))
                if tail is not None:
                    params.append(("tail", f"{tail}"))

                if services is not None and len(services) == 1:
                    service = services[0]
                    return session.get(
                        f"/{project}/{service}/logs",
                        params=params,
                        timeout=0,
                    )
                else:
                    if services:
                        params += [("service", s) for s in services]
                    return session.get(
                        f"/{project}/logs",
                        params=params,
                        timeout=0,
                    )

            async with logs_request() as response:
                close_requested = False

                def signal_handler(signal, frame):
                    nonlocal close_requested
                    close_requested = True
                    asyncio.get_event_loop().call_soon_threadsafe(response.close)

                signal.signal(signal.SIGINT, signal_handler)

                single_service_provided = services is not None and len(services) == 1
                print_function = build_print_function(
                    single_service_provided,
                    None if color is None else (self.use_colors and color),
                    prefix,
                    timestamps,
                )

                try:
                    self.logger.debug("Reading logs")
                    async for line in response.content:
                        fields = json.loads(line.decode())
                        if not self.verbose and fields["levelno"] == logging.DEBUG:
                            continue
                        print_function(fields)
                    return 0
                except asyncio.CancelledError:
                    self.logger.debug("Request cancelled")
                    return 1
                except aiohttp.ClientConnectionError as ex:
                    if not close_requested:
                        self.logger.warning("Connection error {ex}")
                        return 2
            return 0

        return stream_logs_response

    async def attach(self, service: str, context: bool = False):
        server_up = await self.check_server_is_running()

        if server_up:
            return await self.run_client_session(self.make_attach_session(service, context))
        else:
            self.logger.error("Server is not running")
            return 1

    def make_attach_session(self, service: str, context: bool = False):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                data = await response.json()
                project = data["project_name"]

            async with session.ws_connect(
                f"/{project}/{service}/attach",
                params={"format": "json", "context": "on" if context else "no"},
                timeout=0,
            ) as ws:
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
                            fields = json.loads(line)
                            if not self.verbose and fields["levelno"] == logging.DEBUG:
                                continue
                            print_message(fields)
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
                    return 0
                except asyncio.CancelledError:
                    self.logger.debug("Attach cancelled")
                    return 1

        return client_session

    async def images(self, services: Optional[List[str]] = None):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_images_session(services))
        else:
            self.logger.warning("Server is not running")
            self.assure_service_config()
            assert self.service_config is not None
            services_info = [
                {
                    "name": name,
                    "image": get_process_path(resolve_command(get_command(s, self.logger))),
                    "sequence": i,
                }
                for i, (name, s) in enumerate(self.get_services_configs().items())
            ]
            self.show_images(self.working_directory, self.project_name, services_info)

    def make_images_session(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                compose_data = await response.json()
                project = compose_data["project_name"]
                working_directory = compose_data["working_directory"]

            nonlocal services
            if services is None:
                async with session.get(f"/{project}") as response:
                    project_data = await response.json()
                    services = project_data["services"]

            service_data = []
            for s in services:
                async with session.get(f"/{project}/{s}") as response:
                    service_data.append(await response.json())

            self.show_images(working_directory, project, service_data)

        return client_session

    def show_images(self, working_directory, project, service_data):
        service_data.sort(key=lambda x: x["sequence"])

        header = [
            ("name", "NAME", 20),
            ("image", "IMAGE", 60),
        ]
        fmt = ""
        for n, _, w in header:
            fmt += f"{{{n}:{w}}} "
        self.logger.debug(f"Format is '{fmt}'")

        print(f"Project: {project}, in {working_directory}")
        print(fmt.format(**{k: v for k, v, _ in header}))
        for row in service_data:
            print(fmt.format(**row))

    async def ps(self, services: Optional[List[str]] = None, **kwargs):
        server_up = await self.check_server_is_running()

        if server_up:
            await self.run_client_session(self.make_ps_session(services, **kwargs))
        else:
            self.logger.warning("Server is not running")
            self.assure_service_config()
            assert self.service_config is not None
            services_info = [
                {"name": name, "command": get_command(s, self.logger), "state": "-", "sequence": i}
                for i, (name, s) in enumerate(self.get_services_configs().items())
            ]
            self.show_service_status(
                self.working_directory, self.project_name, services_info, **kwargs
            )

    def make_ps_session(self, services: Optional[List[str]] = None, **kwargs):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/") as response:
                compose_data = await response.json()
                project = compose_data["project_name"]
                working_directory = compose_data["working_directory"]

            nonlocal services
            if services is None:
                async with session.get(f"/{project}") as response:
                    project_data = await response.json()
                    services = project_data["services"]

            service_data = []
            for s in services:
                async with session.get(f"/{project}/{s}") as response:
                    service_data.append(await response.json())

            self.show_service_status(working_directory, project, service_data, **kwargs)

        return client_session

    def show_service_status(
        self,
        working_directory,
        project,
        service_data,
        format="default",
        truncate=True,
        quiet=False,
        status=None,
    ):
        service_data.sort(key=lambda x: x["sequence"])
        if status is not None:
            service_data = list(filter(lambda x: x["state"] == status, service_data))

        if quiet:
            service_data = list(map(lambda x: {"name": x["name"]}, service_data))

        if format == "json":
            json.dump(service_data, indent=2, fp=sys.stdout)
            return
        elif quiet:
            for s in service_data:
                print(s["name"])
            return

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

            cmd = " ".join(s["command"]) if isinstance(s["command"], list) else s["command"]
            if truncate and len(cmd) > 30:
                cmd = cmd[:27] + "..."
            row["command"] = cmd

            build_time = s.get("build_time", None)
            since_build = (now - build_time) if build_time is not None else None
            row["created"] = (
                f"{duration_text(since_build)} ago" if since_build is not None else "--"
            )

            state = s["state"]
            if state == "started":
                since_start = (now - s["start_time"]) if s["start_time"] is not None else None
                row["status"] = (
                    f"Up {duration_text(since_start)}" if since_start is not None else "starting"
                )
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
                row["status"] = (
                    f"terminated {duration_text(since_stop)} ago"
                    if since_stop is not None
                    else "terminating"
                )
            else:
                row["status"] = state

            info_rows.append(row)

        print(f"Project: {project}, in {working_directory}")
        print(fmt.format(**{k: v for k, v, _ in header}))
        for row in info_rows:
            print(fmt.format(**row))

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
                working_directory = compose_data["working_directory"]

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

            print(f"Project: {project}, in {working_directory}")
            print(fmt.format(**{k: v for k, v, _ in header}))
            for row in info_rows:
                print(fmt.format(**row))

        return client_session

    async def wait_for_up(self, services=None):
        server_up = False
        while not server_up:
            server_up = await self.check_server_is_running()

        return await self.run_client_session(self.make_wait_for_up_session(services))

    def make_wait_for_up_session(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = ComposeProxy(session, self)
            project = await remote.get_project()

            nonlocal services
            if services is None:
                services = await remote.get_default_services()

            all_services_up = False
            while not all_services_up:
                service_data = []
                for s in services:
                    async with session.get(f"/{project}/{s}") as response:
                        service_data.append(await response.json())

                all_services_up = all([s["state"] == "started" for s in service_data])

        return client_session

    async def wait(self, services=None, down_project=False):
        server_up = await self.check_server_is_running()
        if server_up:
            return await self.run_client_session(self.make_wait_session(services, down_project))
        else:
            self.logger.error("Server is not running")
            return 1

    def make_wait_session(self, services: Optional[List[str]] = None, down_project=False):
        async def client_session(session: aiohttp.ClientSession):
            remote = ComposeProxy(session, self)
            project = await remote.get_project()

            nonlocal services
            if services is None:
                services = await remote.get_default_services()

            any_service_down = False
            while not any_service_down:
                service_data = []
                for s in services:
                    async with session.get(f"/{project}/{s}") as response:
                        service_data.append(await response.json())

                any_service_down = any([s["state"] == "stopped" for s in service_data])

            if any_service_down and down_project:
                self.logger.info("Shutting down after a service went down")
                await self.down()

            return 0

        return client_session

    def get_call_json(self):
        return {
            "project_name": self.project_name,
            "working_directory": str(self.working_directory),
            "service_files": [str(f) for f in self.service_files.paths],
            "create_time": self.create_time,
        }

    async def get_call(self, request):
        return web.json_response(self.get_call_json())

    def get_project_json(self, profiles: List[str]):
        return {
            "services": list(self.services.keys()),
            "default_services": list(self.get_defaults_services()),
            "profile_services": list(self.get_defaults_services(with_profiles=profiles)),
        }

    def _get_project(self, request: web.Request):
        project = request.match_info.get("project", "")
        if project != self.project_name:
            return web.Response(status=404)
        return project

    async def post_stop_project(self, request: web.Request):
        self._get_project(request)
        body = await self._get_json_or_empty(request)
        request_clean = body.get("request_clean", False)

        await self.shutdown(request_clean=request_clean)
        return web.json_response("Stopped")

    def _get_service(self, request: web.Request):
        service = request.match_info.get("service", "")
        if service not in self.services.keys():
            raise web.HTTPNotFound(reason="Service not found")
        return service

    async def _get_json_or_empty(self, request: web.Request):
        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        return body

    async def get_project(self, request: web.Request):
        self._get_project(request)
        profiles: List[str] = request.query.getall("profile", [])
        return web.json_response(self.get_project_json(profiles=profiles))

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
            "image": s.get_process_image(),
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

        response_format = request.query.get("format", "text")
        add_context: bool = request.query.get("context", "no") == "on"
        tail: Optional[int] = make_int(request.query.get("tail"))

        # https://docs.aiohttp.org/en/stable/web_lowlevel.html
        response = web.WebSocketResponse()

        back_stream = WebSocketAdapter(response)
        await response.prepare(request)

        logs_to_response = logging.StreamHandler(back_stream)
        if response_format == "json":
            logs_to_response.setFormatter(JsonFormatter("%(message)s"))

        if add_context:
            self.services[service].feed_handler(logs_to_response, tail=tail)

        self.services[service].attach_log_handlers(logs_to_response, logs_to_response)

        def detach_logger(fut):
            self.logger.debug(f"Detaching log sender from {service}")
            self.services[service].detach_log_handlers(logs_to_response, logs_to_response)

        assert response.task is not None
        response.task.add_done_callback(detach_logger)

        try:
            while not response.closed and not self.services[service].terminated_and_done:
                try:
                    line = await response.receive_str()

                    process = self.services[service].process
                    if process is None or process.stdin is None:
                        self.logger.warning("Cannot forward the input to the process")
                        continue
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

        await self._provide_logs_response(request, [service])

    async def get_services_logs(self, request: web.Request):
        self._get_project(request)
        services: Optional[List[str]] = request.query.getall("service", None)
        if services is None:
            services = list(self.services.keys())

        await self._provide_logs_response(request, services)

    async def _provide_logs_response(self, request: web.Request, services: List[str]):
        response_format: str = request.query.get("format", "text")
        add_context: bool = request.query.get("context", "no") == "on"
        follow: bool = request.query.get("follow", "no") == "on"

        since: Optional[datetime.datetime] = make_date(request.query.get("since"))
        until: Optional[datetime.datetime] = make_date(request.query.get("until"))
        tail: Optional[int] = make_int(request.query.get("tail"))

        # https://docs.aiohttp.org/en/stable/web_lowlevel.html
        response = web.StreamResponse()
        response.enable_chunked_encoding()

        back_stream = ResponseAdapter(response)
        logs_to_response = logging.StreamHandler(back_stream)
        if response_format == "json":
            logs_to_response.setFormatter(JsonFormatter("%(message)s"))

        await response.prepare(request)

        now = datetime.datetime.now()

        if (
            add_context
            or since is not None
            or (until is not None and until < now)
            or tail is not None
        ):
            self.logger.debug(f"Providing log context since={since}, until={until}, tail={tail}")
            log_context = list(
                r
                for s in services
                for r in self.services[s].get_log_context(since=since, until=until, tail=tail)
            )
            log_context.sort(key=lambda x: x.created)
            for r in log_context:
                logs_to_response.emit(r)

        if follow or until is not None and until > now:
            for s in services:
                self.services[s].attach_log_handlers(logs_to_response, logs_to_response)

            def detach_loggers(fut):
                for s in services:
                    self.logger.debug(f"Detaching log sender from {s}")
                    self.services[s].detach_log_handlers(logs_to_response, logs_to_response)

            assert response.task is not None
            response.task.add_done_callback(detach_loggers)

        # Yield for context messages processing
        await asyncio.sleep(0)

        try:
            # TODO: cleaner solution? websocket here?
            while (
                (follow or (until is not None and datetime.datetime.now() < until))
                and not await back_stream.is_broken()
                and not all([self.services[s].terminated_and_done for s in services])
            ):
                await asyncio.sleep(1)
                if until is not None and datetime.datetime.now() > until:
                    break
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
        body = await self._get_json_or_empty(request)
        request_clean = body.get("request_clean", False)
        for s in self.get_stop_sequence([service]):
            if request_clean:
                self.services[s].request_clean()
            await self.services[s].stop()
        return web.json_response("Stopped")

    async def post_restart_project(self, request: web.Request):
        self._get_project(request)
        body = await self._get_json_or_empty(request)
        services = body.get("services")
        if services is None:
            started: Set[str] = {
                name for name, s in self.services.items() if not (s.stopped or s.terminated)
            }
            services = set(self.services.keys()).union(started)

        sequence = services if body.get("no_deps", False) else self.get_stop_sequence(services)
        timeout = body.get("timeout", None)

        await self._restart_in_sequence(sequence, timeout)

        return web.json_response("Restarted")

    async def post_restart_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        body = await self._get_json_or_empty(request)
        sequence = [service] if body.get("no_deps", False) else self.get_stop_sequence([service])
        timeout = body.get("timeout", None)

        await self._restart_in_sequence(sequence, timeout)

        return web.json_response("Restarted")

    async def _restart_in_sequence(self, sequence, timeout=None):
        for s in sequence:
            try:
                await asyncio.wait_for(self.services[s].stop(), timeout)
            except asyncio.TimeoutError:
                await self.services[s].stop()

        for s in reversed(sequence):
            self.services[s].start()

    async def post_kill_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        body = await self._get_json_or_empty(request)
        s = get_signal(body.get("signal", get_default_kill()))
        try:
            await self.services[service].kill(s)
            return web.json_response("Killed")
        except ValueError as ex:
            self.logger.warning(f"ValueError: {ex}")
            return web.json_response(f"ValueError", status=400)
        except ProcessLookupError as ex:
            self.logger.warning(f"ProcessLookupError: {ex}")
            return web.json_response(f"ProcessLookupError", status=400)

    async def post_kill_project(self, request: web.Request):
        self._get_project(request)
        body = await self._get_json_or_empty(request)
        s = get_signal(body.get("signal", get_default_kill()))
        try:
            await asyncio.gather(*[service.kill(s) for service in self.services.values()])
            return web.json_response("Killed")
        except ValueError as ex:
            self.logger.warning(f"ValueError: {ex}")
            return web.json_response(f"ValueError", status=400)
        except ProcessLookupError as ex:
            self.logger.warning(f"ProcessLookupError: {ex}")
            return web.json_response(f"ProcessLookupError", status=400)

    async def post_start_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        body = await self._get_json_or_empty(request)
        request_build = body.get("request_build", False)
        sequence = [service] if body.get("no_deps", False) else self.get_start_sequence([service])
        for s in sequence:
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
                web.post("/{project}/{service}/kill", self.post_kill_service),
                web.post("/{project}/kill", self.post_kill_project),
                web.post("/{project}/{service}/restart", self.post_restart_service),
                web.post("/{project}/restart", self.post_restart_project),
            ]
        )

        return asyncio.get_event_loop().create_task(
            run_server(self.app, self.logger.getChild("http"), self.communication_pipe)
        )

    async def shutdown(self, request_clean=False, retry_on_timeout=True):
        try:
            self.logger.info(" *** Calling terminate on sub processes")
            await_list = []
            for service in self.get_stop_sequence(self.services.keys()):
                if request_clean:
                    self.services[service].request_clean()
                await_list.append(self.services[service].terminate())

            try:
                self.logger.info(f"Waiting {self.shutdown_timeout}s for stop")
                await asyncio.wait_for(asyncio.gather(*await_list), timeout=self.shutdown_timeout)
            except asyncio.TimeoutError:
                self.logger.warning("Shutdown timeout")
                if retry_on_timeout:
                    # Note: second shutdown should trigger instant kill, no retry to break recursion
                    self.shutdown(request_clean=request_clean, retry_on_timeout=False)
        except Exception as ex:
            self.logger.error(f"Exception during shutdown: {ex}, {get_stack_string()}")

    async def watch_services(
        self,
        services: Optional[List[str]] = None,
        start_server=True,
        execute=True,
        execute_build=False,
        execute_clean=False,
        abort_on_exit=False,
        code_from=None,
        attach=None,
        attach_dependencies=False,
        no_attach=None,
        no_deps=False,
        no_log_prefix=False,
        no_color=False,
        timestamps=False,
    ):
        try:
            self.assure_service_config()
        except Exception as ex:
            self.logger.error(f"Exception while loading config: {ex}")
            return -1

        assert self.service_config is not None

        if code_from is not None and code_from not in self.get_services_configs().keys():
            self.logger.error(f"{code_from} is not found among services")
            return -1

        try:
            shared_logging_config = get_shared_logging_config(self.service_config)
            if len(shared_logging_config) > 0:
                shared_logging_config["disable_existing_loggers"] = False
                logging.config.dictConfig(config=shared_logging_config)

            # NOTE: All services are prepared, not all of them are started.
            #       Preparing services early so other methods can use them.
            self.services = {
                name: AsyncProcess(
                    index,
                    name,
                    service_config,
                    self._get_next_color(),
                    execute=execute,
                    execute_build=execute_build,
                    execute_clean=execute_clean,
                    build_args=self.build_args,
                    verbose=self.verbose,
                )
                for (index, (name, service_config)) in enumerate(
                    self.get_services_configs().items()
                )
            }

            server_task: Optional[asyncio.Task] = None
            if start_server:
                server_task = self.start_server()

            service_log_handler = logging.StreamHandler(stream=sys.stderr)
            service_log_handler.setFormatter(logging.Formatter(" **%(name)s* %(message)s"))

            process_log_handler = logging.StreamHandler(stream=sys.stdout)
            process_log_format = build_format(use_prefix=not no_log_prefix, timestamps=timestamps)
            process_log_handler.setFormatter(
                ComposeFormatter(fmt=process_log_format, use_color=self.use_colors and not no_color)
            )

            if services is None:
                input_services = list(self.get_defaults_services())
                start_services = input_services.copy()
            else:
                input_services = services.copy()
                additional = self.get_always_restart_services()
                if len(additional) > 0:
                    self.logger.debug(
                        f"Services with restart policy 'always' are started as well: {additional}"
                    )
                start_services = input_services + additional

            # Restart "always" are treated as dependency for the logs
            log_services = (
                self.get_start_sequence(start_services) if attach_dependencies else input_services
            )
            if attach is not None:
                log_services += attach

            show_logs = {}
            for name in log_services:
                no_attach_allows = no_attach is None or (
                    len(no_attach) > 0 and name not in no_attach
                )
                # Attach_dependencies disables attach limiting behavior
                attach_allows = attach_dependencies or attach is None or name in attach
                show_logs[name] = no_attach_allows and attach_allows

            for name in [name for name, show in show_logs.items() if show]:
                self.services[name].feed_handler(service_log_handler, service=True, process=False)
                self.services[name].feed_handler(process_log_handler, service=False, process=True)
                self.services[name].attach_log_handlers(process_log_handler, service_log_handler)

            async def on_exit_aborter(source_service: AsyncProcess):
                self.logger.debug(f"Aborting after {source_service.name} stop")
                await self.shutdown(request_clean=execute_clean)

            if abort_on_exit:
                for s in self.services.values():
                    s.on_stop.append(on_exit_aborter)

            def signal_handler(signal, frame):
                asyncio.get_event_loop().create_task(self.shutdown(request_clean=execute_clean))

            signal.signal(signal.SIGINT, signal_handler)
            # Well, this is not implemented for Windows
            # asyncio.get_event_loop().add_signal_handler(signal.SIGINT, signal_handler)

            sequence = start_services if no_deps else self.get_start_sequence(start_services)
            for name in sequence:
                self.services[name].start()

            self.logger.debug("Waiting for services")
            await asyncio.gather(*[s.watch() for s in self.services.values()])
            self.logger.debug("Services closed")
        except Exception as ex:
            self.logger.debug(get_stack_string())
            self.logger.debug(f"Exception during watching services: {ex}")
            await self.shutdown()

        # It is here to prevent too early server stop when closing...
        # Even short sleeps are probably enough, so the handler can take the event loop.
        for _ in range(5):
            await asyncio.sleep(0)
        if self.app is not None:
            await self.app.shutdown()

        if code_from is not None:
            return self.services[code_from].rc
        else:
            return 0

    def get_always_restart_services(self):
        return [
            name for name, service in self.services.items() if service.restart_policy == "always"
        ]

    async def run_client_session(self, session_function):
        try:
            return await self.execute_client_session(session_function)
        except ClientConnectorError as ex:
            self.logger.error(f"Connection error for {self.communication_pipe}: {ex}")
        except FileNotFoundError as ex:
            self.logger.error(f"Name error for {self.communication_pipe}: {ex}")
        except ClientResponseError as ex:
            self.logger.error(f"Error response: {ex}")
        return 3

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


async def get_project_and_services(session: aiohttp.ClientSession):
    project = await get_project(session)
    existing_services = await get_services(session, project)
    return project, existing_services


async def get_project(session: aiohttp.ClientSession):
    response = await session.get(f"/")
    data = await response.json()
    return data["project_name"]


async def get_services(session: aiohttp.ClientSession, project):
    response = await session.get(f"/{project}")
    data = await response.json()
    return data["services"]


class ComposeProxy:
    def __init__(self, session: aiohttp.ClientSession, compose: Compose) -> None:
        self.session = session
        self.compose = compose

        self.project = compose.project_name

        self.directory_data: Optional[dict] = None
        self.project_data: Optional[dict] = None

    async def get_project(self):
        async with self.session.get(f"/") as response:
            self.directory_data = await response.json()

        if self.directory_data is not None:
            self.project = self.directory_data["project_name"]

        return self.project

    async def get_project_data(self, project=None):
        if project is None:
            project = self.project
        if project is None:
            project = await self.get_project()

        async with self.session.get(
            f"/{project}", params={"profile": self.compose.profiles}
        ) as response:
            self.project_data = await response.json()

        return self.project_data

    async def get_default_services(self):
        data = await self.get_project_data()
        return data["profile_services"] if self.compose.profiles else data["default_services"]


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
            handle_signals=False,
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
                    # NOTE: this is not convertible back to a yaml, but can work...
                    for k, v in value.items():
                        base_value.append((k, v))
                else:
                    base_value += [value]
            elif isinstance(base_value, dict):
                if isinstance(value, dict):
                    base_value.update(value)
                elif isinstance(value, list):
                    # NOTE: fallback to more general list
                    base_value = [(k, v) for k, v in base_value.items()] + value
                    base[key] = base_value
                else:
                    # TODO just throw? this is weird
                    print("dict vs value")
                    base_value[f"{value}"] = None
            else:
                base[key] = value
    return base
