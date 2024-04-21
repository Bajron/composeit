import asyncio
import sys
import signal
import time
import logging
import pathlib
import hashlib
import json
import termcolor

from .service_config import merge_configs
from .log_utils import ComposeFormatter, build_format, build_print_function

from .service_config import (
    ServiceFiles,
    get_shared_logging_config,
    get_command,
    get_process_path,
    resolve_command,
)
from .process import AsyncProcess
from .graph import topological_sequence

from .utils import (
    interpolate_variables,
    duration_text,
    cumulative_time_text,
    date_time_text,
    get_stack_string,
)

from .client import ComposeProxy
from .server import ComposeServer
from typing import List, Dict, Optional, Iterable, Mapping, Set, Any

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

        self.communication_pipe: str = get_communication_path(working_directory, project_name)

        self.logger.debug(f"Working directory: {self.working_directory}")
        self.logger.debug(f"Project name: {self.project_name}")
        self.logger.debug(f"Communication pipe: {self.communication_pipe}")

        self.use_colors: bool = use_color
        self.color_assigner: ColorAssigner = ColorAssigner()

        self.service_files: ServiceFiles = service_files
        self.service_config: Optional[dict] = None

        self.profiles: List[str] = profiles or []
        self.logger.debug(f"Profiles: {self.profiles}")

        self.depending: Dict[str, Iterable[str]] = {}
        self.depends: Dict[str, Iterable[str]] = {}

        self.shutdown_timeout: float = timeout

        if not defer_config_load:
            self.load_service_config()

        self.build_args: Dict[str, str] = build_args or {}

        self.services: Dict[str, AsyncProcess] = {}

        self.wait_for_services: Optional[asyncio.Future] = None
        self.reload_in_progress: bool = False

        self.proxy = ComposeProxy(
            self.logger.getChild("proxy"),
            self.verbose,
            self.communication_pipe,
            project_name=self.project_name,
            profiles=self.profiles,
        )

        self.server = ComposeServer(self, self.logger.getChild("server"))

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

    def get_default_services(self, with_profiles: Optional[List[str]] = None) -> Iterable[str]:
        profiles = with_profiles if with_profiles is not None else self.profiles
        return [
            key
            for key, service in self.get_services_configs().items()
            if "profile" not in service or service["profile"] in profiles
        ]

    def get_service_ids_in_config(self):
        return list(self.get_services_configs().keys())

    def get_service_ids(self) -> List[str]:
        return list(self.services.keys())

    def _get_next_color(self):
        return self.color_assigner.next() if self.use_colors else None

    def _parse_service_config(
        self,
        check_consistency=True,
        interpolate=True,
        normalize=True,
        resolve_paths=True,
        reload=False,
    ):
        parsed_data = merge_configs(self.service_files.get_parsed_files(reload))
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

        self.depending.update(depending)
        self.depends.update(depends)

    def start_service(self, service: str, request_build: bool = False, no_deps: bool = False):
        sequence = [service] if no_deps else self.get_start_sequence([service])
        for s in sequence:
            if request_build:
                self.services[s].request_build()
            # Last one in the sequence should be `service`
            started = self.services[s].start()
        return started

    async def stop_service(self, service: str, request_clean: bool = False):
        for s in self.get_stop_sequence([service]):
            if request_clean:
                self.services[s].request_clean()
            await self.services[s].stop()

    def get_start_sequence(self, services: Iterable[str]) -> List[str]:
        assert self.depends is not None
        return topological_sequence(services, self.depends)

    def get_stop_sequence(self, services: Iterable[str]) -> List[str]:
        assert self.depending is not None
        return topological_sequence(services, self.depending)

    def get_always_restart_services(self):
        return [
            name
            for name, service in self.services.items()
            if service.get_restart_policy() == "always"
        ]

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
        no_server=False,
    ):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.start(services, execute_build, no_deps)
        else:
            return await self.watch_services(
                services=services,
                start_server=not no_server,
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

    async def down(self, services=None, timeout=None):
        server_up = await self.proxy.check_server_is_running()
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
            server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.stop(services, request_clean, timeout)
        else:
            self.logger.error("Server is not running")
            return 2

    async def restart(self, services=None, **kwargs):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.restart(services, **kwargs)
        else:
            self.logger.error("Server is not running")
            return 2

    async def kill(self, services=None, signal=9):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.kill(services, signal)
        else:
            self.logger.error("Server is not running")
            return 2

    async def logs(
        self,
        services: Optional[List[str]] = None,
        color: Optional[bool] = None,
        timestamps: Optional[bool] = None,
        prefix: Optional[bool] = None,
        **kwargs,
    ):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            single_service_provided = services is not None and len(services) == 1
            print_function = build_print_function(
                single_service_provided,
                None if color is None else (self.use_colors and color),
                prefix,
                timestamps,
            )
            return await self.proxy.logs(services, print_function=print_function, **kwargs)
        else:
            self.logger.error("Server is not running")
            return 2

    async def attach(self, service: str, context: bool = False):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.attach(service, context)
        else:
            self.logger.error("Server is not running")
            return 2

    async def images(self, services: Optional[List[str]] = None):
        server_up = await self.proxy.check_server_is_running()

        if server_up:
            directory, name, services_info = await self.proxy.images(services)
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
            directory, name = self.working_directory, self.project_name

        self.show_images(directory, name, services_info)

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
        server_up = await self.proxy.check_server_is_running()

        if server_up:
            name, directory, services_info = await self.proxy.ps(services)
        else:
            self.logger.warning("Server is not running")
            self.assure_service_config()
            assert self.service_config is not None
            services_info = [
                {"name": name, "command": get_command(s, self.logger), "state": "-", "sequence": i}
                for i, (name, s) in enumerate(self.get_services_configs().items())
            ]
            name, directory = self.project_name, self.working_directory

        self.print_ps(
            directory,
            name,
            services_info,
            **kwargs,
        )

    def print_ps(
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
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            name, directory, processes = await self.proxy.top(services)
        else:
            self.logger.warning("Server is not running")
            name, directory, processes = self.project_name, self.working_directory, []

        self.print_top(name, directory, processes)

    def print_top(self, project, working_directory, processes):
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
            return {k: v or "-" for k, v in row.items()}

        info_rows = []
        for process_info in processes:
            info_rows.append(make_row(process_info))

        print(f"Project: {project}, in {working_directory}")
        print(fmt.format(**{k: v for k, v, _ in header}))
        for row in info_rows:
            print(fmt.format(**row))

    async def server_info(self, wait: bool = False, wait_timeout: Optional[float] = None):
        code, info = await self.proxy.server_info(wait, wait_timeout)
        if code == 0:
            print(info)
            return 0
        elif code == 1:
            print("Server is not running")
            return 2

    async def wait_for_up(self, services=None):
        await self.proxy.wait_for_server()
        return await self.proxy.wait_for_up(services)

    async def wait(self, services=None, down_project=False):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            any_service_down = await self.proxy.wait(services)
            if any_service_down and down_project:
                self.logger.info("Shutting down after a service went down")
                await self.down()
        else:
            self.logger.error("Server is not running")
            return 2

    async def reload(self):
        server_up = await self.proxy.check_server_is_running()
        if server_up:
            return await self.proxy.reload()
        else:
            self.logger.error("Server is not running")
            return 2

    def get_call_json(self):
        return {
            "project_name": self.project_name,
            "working_directory": str(self.working_directory),
            "service_files": [str(f) for f in self.service_files.paths],
            "create_time": self.create_time,
        }

    def get_project_json(self, profiles: List[str]):
        return {
            "services": list(self.services.keys()),
            "default_services": list(self.get_default_services()),
            "profile_services": list(self.get_default_services(with_profiles=profiles)),
        }

    def get_service_json(self, service_name):
        s = self.services[service_name]
        j = {
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
        self.logger.debug(f"{j}")
        return j

    async def restart_services(
        self,
        services: Optional[List[str]] = None,
        no_deps: bool = False,
        timeout: Optional[float] = None,
    ):
        if services is None:
            started: Set[str] = {
                name for name, s in self.services.items() if not (s.stopped or s.terminated)
            }
            services = list(set(self.services.keys()).union(started))

        sequence = services if no_deps else self.get_stop_sequence(services)
        await self.restart_in_sequence(sequence, timeout)

    async def restart_in_sequence(self, sequence: List[str], timeout: Optional[float] = None):
        for s in sequence:
            try:
                # NOTE: shielded, because we want this stop to run, so the second one is force stop
                await asyncio.wait_for(asyncio.shield(self.services[s].stop()), timeout)
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout when stopping {s}")
                await self.services[s].stop()

        for s in reversed(sequence):
            self.services[s].start()

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
        start_server: bool = True,
        execute: bool = True,
        execute_build: bool = False,
        execute_clean: bool = False,
        abort_on_exit: bool = False,
        code_from: Optional[str] = None,
        attach: Optional[List[str]] = None,
        attach_dependencies: bool = False,
        no_attach: Optional[List[str]] = None,
        no_deps: bool = False,
        no_log_prefix: bool = False,
        no_color: bool = False,
        timestamps: bool = False,
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
                self.logger.debug("Starting server")
                server_task = self.server.start_server()

            service_log_handler = logging.StreamHandler(stream=sys.stderr)
            service_log_handler.setFormatter(logging.Formatter(" **%(name)s* %(message)s"))

            process_log_handler = logging.StreamHandler(stream=sys.stdout)
            process_log_format = build_format(use_prefix=not no_log_prefix, timestamps=timestamps)
            process_log_handler.setFormatter(
                ComposeFormatter(fmt=process_log_format, use_color=self.use_colors and not no_color)
            )

            input_services, start_services = self._get_input_and_start_services(services)
            show_logs = self._get_services_to_show(
                input_services, start_services, attach, attach_dependencies, no_attach
            )

            def attach_log_handlers(names):
                for name in names:
                    self.services[name].feed_handler(
                        service_log_handler, service=True, process=False
                    )
                    self.services[name].feed_handler(
                        process_log_handler, service=False, process=True
                    )
                    self.services[name].attach_log_handlers(
                        process_log_handler, service_log_handler
                    )

            attach_log_handlers([name for name, show in show_logs.items() if show])

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

            while True:
                self.logger.debug("Waiting for services")
                self.wait_for_services = asyncio.shield(
                    asyncio.gather(*[s.run() for s in self.services.values()])
                )
                try:
                    await self.wait_for_services
                    break
                except asyncio.CancelledError:
                    self.wait_for_services = None
                    self.logger.info("Waiting for services interrupted")

                    if self.reload_in_progress:
                        # TODO: Should these come from here or from the start request
                        added_services = self.add_services(execute, execute_build, execute_clean)
                        # TODO: take care of orphans eventually as an option
                        orphaned_services = self.check_orphans()

                        input_services, start_services = self._get_input_and_start_services(
                            services
                        )
                        show_logs = self._get_services_to_show(
                            input_services, start_services, attach, attach_dependencies, no_attach
                        )

                        to_attach = [name for name, show in show_logs.items() if show]
                        attach_log_handlers([name for name in to_attach if name in added_services])

                        sequence = (
                            start_services if no_deps else self.get_start_sequence(start_services)
                        )
                        for name in [name for name in sequence if name in added_services]:
                            self.services[name].start()

                        # NOTE: reload behaves like up actually...

                        self.reload_in_progress = False
                    else:
                        raise

            self.logger.debug("Services closed")
        except Exception as ex:
            self.logger.debug(get_stack_string())
            self.logger.debug(f"Exception during watching services: {ex}")
            await self.shutdown()

        # It is here to prevent too early server stop when closing...
        # Even short sleeps are probably enough, so the handler can take the event loop.
        for _ in range(5):
            await asyncio.sleep(0)
        if self.server is not None:
            await self.server.shutdown()

        if code_from is not None:
            return self.services[code_from].rc
        else:
            return 0

    def _get_input_and_start_services(self, services: Optional[List[str]] = None):
        if services is None:
            input_services = list(self.get_default_services())
            start_services = input_services.copy()
        else:
            input_services = services.copy()
            additional = self.get_always_restart_services()
            if len(additional) > 0:
                self.logger.debug(
                    f"Services with restart policy 'always' are started as well: {additional}"
                )
            start_services = input_services + additional
        return input_services, start_services

    def _get_services_to_show(
        self,
        input_services: List[str],
        start_services: List[str],
        attach: Optional[List[str]] = None,
        attach_dependencies: bool = False,
        no_attach: Optional[List[str]] = None,
    ):
        # Restart "always" are treated as dependency for the logs
        log_services = (
            self.get_start_sequence(start_services) if attach_dependencies else input_services
        )
        if attach is not None:
            log_services += attach

        show_logs = {}
        for name in log_services:
            no_attach_allows = no_attach is None or (len(no_attach) > 0 and name not in no_attach)
            # Attach_dependencies disables attach limiting behavior
            attach_allows = attach_dependencies or attach is None or name in attach
            show_logs[name] = no_attach_allows and attach_allows

        return show_logs

    async def trigger_reload(self):
        if self.reload_in_progress:
            self.logger.debug("Reload requested when reload is in progress")
            return

        self.reload_in_progress = True
        self.reload_configuration()

        new_services = self.get_not_created_services()
        if self.wait_for_services and len(new_services) > 0:
            self.logger.debug("Interrupting waiting for services")
            self.wait_for_services.cancel()
        else:
            self.reload_in_progress = False

    def reload_configuration(self):
        self.load_service_config(reload=True)
        services_configs = self.get_services_configs()

        for k, v in self.services.items():
            if k in services_configs:
                v.update_config(services_configs[k])

    def get_not_created_services(self) -> List[str]:
        created_services = self.get_service_ids()
        config_services = self.get_service_ids_in_config()

        new_services = list(set(config_services).difference(set(created_services)))
        return new_services

    def add_services(self, execute=True, execute_build=False, execute_clean=False):
        new_services = self.get_not_created_services()

        if len(new_services) > 0:
            self.logger.info(f"{len(new_services)} new services detected")
            services_configs = self.get_services_configs()

            services_count = len(self.services)
            services_to_add = {
                name: AsyncProcess(
                    services_count + index,
                    name,
                    services_configs[name],
                    self._get_next_color(),
                    execute=execute,
                    execute_build=execute_build,
                    execute_clean=execute_clean,
                    build_args=self.build_args,
                    verbose=self.verbose,
                )
                for (index, name) in enumerate(new_services)
            }

            self.services.update(services_to_add)

        return new_services

    def check_orphans(self):
        created_services = self.get_service_ids()
        config_services = self.get_service_ids_in_config()

        orphaned_services = list(set(created_services).difference(set(config_services)))

        if len(orphaned_services) > 0:
            self.logger.warning(
                f"{len(orphaned_services)} orphaned services detected: {orphaned_services}"
            )

        return orphaned_services


def get_communication_path(directory_path: pathlib.Path, project_name: Optional[str] = None) -> str:
    if project_name is None:
        project_name = directory_path.name

    if sys.platform == "win32":
        h = hashlib.sha256(str(directory_path.resolve()).encode()).hexdigest()
        return r"\\.\pipe\composeit_" + f"{project_name}_{h}"

    return str(directory_path / f".{project_name}.daemon")
