import aiohttp
import logging
import datetime
import json
import sys
import signal
import time
import asyncio
import aiohttp
from aiohttp import ClientConnectorError, ClientResponseError
from socket import gethostname

from typing import List, Dict, Optional, Callable

from .log_utils import print_message


class ComposeProxy:
    def __init__(
        self,
        logger: logging.Logger,
        verbose: bool,
        connection_path: str,
        project_name: Optional[str] = None,
        profiles: List[str] = [],
    ) -> None:

        self.connection_path: str = connection_path
        self.logger: logging.Logger = logger
        self.verbose: bool = verbose
        self.project_name: Optional[str] = project_name
        self.profiles: List[str] = profiles

        # return await self.run_client_session(client_session)

    async def server_info(self, wait: bool = False, wait_timeout: Optional[float] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)

            sleep_time = 0.05
            end = time.time() + wait_timeout if wait_timeout is not None else None
            interrupted = False

            def signal_handler(signal, frame):
                nonlocal end, interrupted
                end = time.time()
                interrupted = True

            signal.signal(signal.SIGINT, signal_handler)

            while end is None or time.time() < end:
                try:
                    directory = await remote.get_directory_data()
                    return 0, directory
                except:
                    pass

                if not wait or interrupted:
                    break

                # Last try will most likely not happen
                if end is not None:
                    sleep_time = min(max(0, end - time.time()), sleep_time)
                time.sleep(sleep_time)
                sleep_time = min(2 * sleep_time, 1)

            if interrupted:
                return 2, {}

            return 1, {}

        return await self.run_client_session(client_session)

    async def wait(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            await remote.get_project()

            nonlocal services
            if services is None:
                services = await remote.get_default_services()

            any_service_down = False
            while not any_service_down:
                service_data = await remote.get_services_data(services)
                any_service_down = any([s["state"] == "stopped" for s in service_data])

            return any_service_down

        return await self.run_client_session(client_session)

    async def wait_for_up(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            await remote.get_project()

            nonlocal services
            if services is None:
                services = await remote.get_default_services()

            all_services_up = False
            while not all_services_up:
                service_data = await remote.get_services_data(services)
                all_services_up = all([s["state"] == "started" for s in service_data])

            return all_services_up

        return await self.run_client_session(client_session)

    async def start(self, services=None, request_build=False, no_deps=False):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            existing_services = await remote.get_services()

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
                response = await remote.start_service(service, request_build, no_deps)
                if response.status == 200:
                    message = await response.json()
                    self.logger.info(f"{service}: {message}")
                else:
                    bad_responses += 1

            return 0 if bad_responses == 0 else 1

        return await self.run_client_session(run_client_commands)

    async def images(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            directory_data = await remote.get_directory_data()
            project = directory_data["project_name"]
            working_directory = directory_data["working_directory"]

            nonlocal services
            if services is None:
                services = await remote.get_services()

            service_data = await remote.get_services_data(services)

            return working_directory, project, service_data

        return await self.run_client_session(client_session)

    async def top(self, services: Optional[List[str]] = None):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            directory_data = await remote.get_directory_data()
            project = directory_data["project_name"]
            working_directory = directory_data["working_directory"]

            nonlocal services
            if services is None:
                services = await remote.get_services()

            processes = await remote.get_services_top(services)

            return project, working_directory, processes

        return await self.run_client_session(client_session)

    async def ps(self, services: Optional[List[str]] = None, **kwargs):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            directory_data = await remote.get_directory_data()
            project = directory_data["project_name"]
            working_directory = directory_data["working_directory"]

            nonlocal services
            if services is None:
                services = await remote.get_services()

            services_data = await remote.get_services_data(services)
            return project, working_directory, services_data

        return await self.run_client_session(client_session)

    async def attach(
        self, service: str, context: bool = False, printer=print_message, inputer=sys.stdin.readline
    ):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            async with await remote.attach_to_service(service, context) as ws:
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
                            printer(fields)
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
                        input_line = await loop.run_in_executor(None, inputer)
                        if not ws.closed:
                            await ws.send_str(input_line)
                    return 0
                except asyncio.CancelledError:
                    self.logger.debug("Attach cancelled")
                    return 1

        return await self.run_client_session(client_session)

    async def logs(
        self,
        services: Optional[List[str]] = None,
        print_function: Optional[Callable] = None,
        follow: bool = False,
        context: Optional[bool] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        tail: Optional[int] = None,
    ):
        async def stream_logs_response(session: aiohttp.ClientSession):
            remote = self.make_client(session)

            async def logs_request():
                common_args = dict(
                    follow=follow, context=context, since=since, until=until, tail=tail
                )
                if services is not None and len(services) == 1:
                    return await remote.open_service_logs(services[0], **common_args)
                else:
                    return await remote.open_logs(services=services, **common_args)

            async with await logs_request() as response:
                close_requested = False

                def signal_handler(signal, frame):
                    nonlocal close_requested
                    close_requested = True
                    asyncio.get_event_loop().call_soon_threadsafe(response.close)

                signal.signal(signal.SIGINT, signal_handler)

                try:
                    self.logger.debug("Reading logs")
                    async for line in response.content:
                        fields = json.loads(line.decode())
                        if not self.verbose and fields["levelno"] == logging.DEBUG:
                            continue
                        if print_function is not None:
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

        return await self.run_client_session(stream_logs_response)

    async def kill(self, services=None, signal=9):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            project = await remote.get_project()
            existing_services = await remote.get_services()

            if services is not None:
                for service in services:
                    if service not in existing_services:
                        self.logger.warning(f"{service} does not exist on the server, skipping")
                        continue
                    response = await remote.kill_service(service, signal)
                    message = await response.json()
                    self.logger.info(f"{service}: {message}")
            else:
                response = await remote.kill_project(signal)
                message = await response.json()
                self.logger.info(f"{project}: {message}")

            return 0 if 200 <= response.status < 300 else 1

        return await self.run_client_session(run_client_commands)

    async def restart(self, services=None, no_deps=False, timeout=None):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            project = await remote.get_project()
            if services is not None and len(services) == 1:
                response = await remote.restart_service(
                    services[0], no_deps=no_deps, timeout=timeout
                )
                if response.status == 200:
                    message = await response.json()
                    self.logger.info(f"{services[0]}: {message}")
            else:
                response = await remote.restart_project(
                    no_deps=no_deps, timeout=timeout, services=services
                )
                if response.status == 200:
                    message = await response.json()
                    self.logger.info(f"{project}: {message}")

            return 0 if 200 <= response.status < 300 else 1

        return await self.run_client_session(run_client_commands)

    async def stop(self, services=None, request_clean=False, timeout=None):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            project = await remote.get_project()
            existing_services = await remote.get_services()

            if services is not None:
                for service in services:
                    if service not in existing_services:
                        self.logger.warning(f"{service} does not exist on the server, skipping")
                        continue

                    self.logger.debug(f"Stopping /{project}/{service}")
                    request = lambda: remote.stop_service(service, request_clean=request_clean)

                    try:
                        response = await asyncio.wait_for(
                            request(),
                            timeout=timeout,
                        )
                    except asyncio.exceptions.TimeoutError:
                        self.logger.warning(f"Stopping {service} timed out after {timeout}")
                        response = await request()

                    if response.status == 200:
                        message = await response.json()
                        self.logger.info(f"{service}: {message}")
            else:
                request = lambda: remote.stop_project(request_clean=request_clean)

                try:
                    response = await asyncio.wait_for(request(), timeout=timeout)
                except asyncio.exceptions.TimeoutError:
                    self.logger.warning(f"Stopping {project} timed out after {timeout}")
                    response = await request()

                if response.status == 200:
                    message = await response.json()
                    self.logger.info(f"{project}: {message}")

            return 0 if 200 <= response.status < 300 else 1

        return await self.run_client_session(run_client_commands)

    async def reload(self):
        async def run_client_commands(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            response = await remote.reload_project()
            if response.status == 200:
                message = await response.json()
                self.logger.info(f"{remote.project}: {message}")

            return 0 if 200 <= response.status < 300 else 1

        return await self.run_client_session(run_client_commands)

    async def check_server_is_running(self):
        try:
            data = await self.execute_client_session(
                self.make_server_check(), connector_options={"force_close": True}
            )
            if not data:
                return False

            if data["project_name"] != self.project_name:
                raise Exception(
                    f"Unexpected project received from the server. Received {data['project_name']} while expecting {self.project_name}"
                )

            return True
        except asyncio.TimeoutError as ex:
            self.logger.warning(f"Timeout in server check assuming server is up")
            return True
        except ClientConnectorError as ex:
            pass
        except FileNotFoundError as ex:
            pass
        return False

    def make_server_check(self):
        async def client_session(session: aiohttp.ClientSession):
            async with session.get(f"/", timeout=5) as response:
                return await response.json()

        return client_session

    async def wait_for_server(self):
        await self.run_client_session(self.make_wait_for_server())

    def make_wait_for_server(self):
        async def client_session(session: aiohttp.ClientSession):
            remote = self.make_client(session)
            server_up = False
            while not server_up:
                try:
                    await remote.get_directory_data(timeout=5)
                    server_up = True
                except:
                    pass

        return client_session

    async def run_client_session(self, session_function):
        try:
            return await self.execute_client_session(session_function)
        except ClientConnectorError as ex:
            self.logger.error(f"Connection error for {self.connection_path}: {ex}")
        except FileNotFoundError as ex:
            self.logger.error(f"Name error for {self.connection_path}: {ex}")
        except ClientResponseError as ex:
            self.logger.error(f"Error response: {ex}")
        return 3

    async def execute_client_session(self, session_function, connector_options=None):
        async def with_connector(connector):
            hostname = gethostname()
            async with aiohttp.ClientSession(
                base_url=f"http://{hostname}",
                connector=connector,
                raise_for_status=True,
                timeout=aiohttp.ClientTimeout(sock_connect=10, connect=10),
            ) as session:
                return await session_function(session)

        return await self.execute_with_connector(with_connector, **(connector_options or {}))

    async def execute_with_connector(self, connector_function, **kwargs):
        self.logger.debug(f"Connect: {self.connection_path}")
        if sys.platform == "win32":
            async with aiohttp.NamedPipeConnector(path=self.connection_path, **kwargs) as connector:
                return await connector_function(connector)
        else:
            async with aiohttp.UnixConnector(path=self.connection_path, **kwargs) as connector:
                return await connector_function(connector)

    def make_client(self, session: aiohttp.ClientSession):
        return ComposeClient(
            logger=self.logger.getChild("client"),
            session=session,
            project_name=self.project_name,
            profiles=self.profiles,
        )


class ComposeClient:
    def __init__(
        self,
        logger: logging.Logger,
        session: aiohttp.ClientSession,
        project_name: Optional[str] = None,
        profiles: List[str] = [],
    ) -> None:
        self.session: aiohttp.ClientSession = session
        self.logger: logging.Logger = logger

        self.project: Optional[str] = project_name
        self.project_confirmed: bool = False

        self.profiles: List[str] = profiles

        self.directory_data: Optional[dict] = None
        self.project_data: Dict[str, dict] = {}

    async def get_directory_data(self, **kwargs):
        async with self.session.get(f"/", **kwargs) as response:
            self.directory_data = await response.json()
        return self.directory_data

    async def get_project(self):
        await self.get_directory_data()

        if self.directory_data is not None:
            assert self.project == self.directory_data["project_name"]
            self.project = self.directory_data["project_name"]
            self.project_confirmed = True
        else:
            self.project_confirmed = False

        return self.project

    async def _assure_project(self, project=None):
        if project is None and self.project_confirmed:
            project = self.project
        if project is None:
            project = await self.get_project()
        return project

    async def get_project_data(self, project=None):
        project = await self._assure_project(project)
        async with self.session.get(f"/{project}", params={"profile": self.profiles}) as response:
            self.project_data[project] = await response.json()

        return self.project_data[project]

    async def get_default_services(self):
        data = await self.get_project_data()
        return data["profile_services"] if self.profiles else data["default_services"]

    async def get_services(self):
        data = await self.get_project_data()
        return data["services"]

    async def get_services_data(self, services: List[str]):
        project = await self._assure_project()
        service_data: List[dict] = []
        for service in services:
            async with self.session.get(f"/{project}/{service}") as response:
                service_data.append(await response.json())
        return service_data

    async def get_services_top(self, services: List[str]):
        project = await self._assure_project()
        process_data: List[dict] = []
        for service in services:
            async with self.session.get(f"/{project}/{service}/top") as response:
                process_data.extend(await response.json())
        return process_data

    async def start_service(self, service: str, request_build: bool, no_deps: bool):
        project = await self._assure_project()
        return await self.session.post(
            f"/{project}/{service}/start",
            json={"request_build": request_build, "no_deps": no_deps},
        )

    async def stop_service(self, service: str, request_clean: bool):
        project = await self._assure_project()
        return await self.session.post(
            f"/{project}/{service}/stop",
            json={"request_clean": request_clean},
        )

    async def stop_project(self, request_clean: bool):
        project = await self._assure_project()
        return await self.session.post(
            f"/{project}/stop",
            json={"request_clean": request_clean},
        )

    async def restart_project(self, no_deps: bool, timeout=None, services=None):
        project = await self._assure_project()
        specification = {"no_deps": no_deps}
        if timeout is not None:
            specification["timeout"] = timeout
        if services is not None:
            specification["services"] = services

        return await self.session.post(
            f"/{project}/restart",
            json=specification,
        )

    async def reload_project(self):
        project = await self._assure_project()

        return await self.session.post(f"/{project}/reload")

    async def restart_service(self, service, no_deps: bool, timeout=None):
        project = await self._assure_project()
        specification = {"no_deps": no_deps}
        if timeout is not None:
            specification["timeout"] = timeout

        return await self.session.post(
            f"/{project}/{service}/restart",
            json=specification,
        )

    async def kill_project(self, signal):
        project = await self._assure_project()
        self.logger.debug(f"Kill {signal} to /{project}")
        return await self.session.post(
            f"/{project}/kill",
            json={"signal": signal},
        )

    async def kill_service(self, service, signal):
        project = await self._assure_project()
        self.logger.debug(f"Kill {signal} to /{project}/{service}")
        return await self.session.post(
            f"/{project}/{service}/kill",
            json={"signal": signal},
        )

    def _logs_params(
        self,
        follow: bool = False,
        context: Optional[bool] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        tail: Optional[int] = None,
    ):
        if context is None:
            context = not follow

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

        return params

    async def open_service_logs(
        self,
        service: str,
        follow: bool = False,
        context: Optional[bool] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        tail: Optional[int] = None,
    ):
        project = await self._assure_project()
        params = self._logs_params(follow, context, since, until, tail)
        return self.session.get(
            f"/{project}/{service}/logs",
            params=params,
            timeout=0,
        )

    async def open_logs(
        self,
        services: Optional[List[str]] = None,
        follow: bool = False,
        context: Optional[bool] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        tail: Optional[int] = None,
    ):
        project = await self._assure_project()
        params = self._logs_params(follow, context, since, until, tail)
        if services:
            params += [("service", s) for s in services]
        return self.session.get(
            f"/{project}/logs",
            params=params,
            timeout=0,
        )

    async def attach_to_service(self, service: str, context: bool):
        project = await self._assure_project()
        return self.session.ws_connect(
            f"/{project}/{service}/attach",
            params={"format": "json", "context": "on" if context else "no"},
            timeout=0,
        )
