from .service_config import get_signal, get_default_kill
from .log_utils import JsonFormatter
from .web_utils import ResponseAdapter, WebSocketAdapter
from .utils import make_int, make_date
from json.decoder import JSONDecodeError

import datetime
import os
import sys
import logging
import asyncio
from aiohttp import web
from aiohttp.web import (
    cast,
    Application,
    AppRunner,
    AccessLogger,
    NamedPipeSite,
    UnixSite,
    BaseSite,
)

from typing import List, Optional


class ComposeServer:
    def __init__(self, compose, logger: logging.Logger) -> None:
        # FIXME: yeah... not nice. Avoiding circual import and getting type
        #        Proper Protocol or ABC should be here I guess.
        #        Would be good to remove direct calls to self.compose.services btw.
        from .compose import Compose

        self.compose: Compose = compose
        self.project_name = compose.project_name
        self.connection_path = compose.communication_pipe
        self.logger = logger

        self.app: Optional[Application] = None

    def _get_project(self, request: web.Request):
        project = request.match_info.get("project", "")
        if project != self.project_name:
            return web.Response(status=404)
        return project

    def _get_service(self, request: web.Request):
        service = request.match_info.get("service", "")
        if service not in self.compose.get_service_ids():
            raise web.HTTPNotFound(reason="Service not found")
        return service

    async def _get_json_or_empty(self, request: web.Request):
        try:
            body = await request.json()
        except JSONDecodeError:
            body = {}
        return body

    async def get_call(self, request):
        return web.json_response(self.compose.get_call_json())

    async def get_project(self, request: web.Request):
        self._get_project(request)
        profiles: List[str] = request.query.getall("profile", [])
        return web.json_response(self.compose.get_project_json(profiles=profiles))

    async def get_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        return web.json_response(self.compose.get_service_json(service))

    async def get_service_processes(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        return web.json_response(self.compose.services[service].get_processes_info())

    async def post_stop_project(self, request: web.Request):
        self._get_project(request)
        body = await self._get_json_or_empty(request)
        request_clean = body.get("request_clean", False)

        await self.compose.shutdown(request_clean=request_clean)
        return web.json_response("Stopped")

    async def post_kill_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        body = await self._get_json_or_empty(request)
        s = get_signal(body.get("signal", get_default_kill()))
        try:
            await self.compose.services[service].kill(s)
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
            await asyncio.gather(*[service.kill(s) for service in self.compose.services.values()])
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

        started = self.compose.start_service(
            service, request_build, no_deps=body.get("no_deps", False)
        )
        message = "Started" if started else "Running"

        return web.json_response(message)

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

        services = self.compose.services

        if add_context:
            services[service].feed_handler(logs_to_response, tail=tail)

        services[service].attach_log_handlers(logs_to_response, logs_to_response)

        def detach_logger(fut):
            self.logger.debug(f"Detaching log sender from {service}")
            services[service].detach_log_handlers(logs_to_response, logs_to_response)

        assert response.task is not None
        response.task.add_done_callback(detach_logger)

        try:
            while not response.closed and not services[service].terminated_and_done:
                try:
                    line = await response.receive_str()

                    process = services[service].process
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
            services = self.compose.get_service_ids()

        await self._provide_logs_response(request, services)

    async def _provide_logs_response(self, request: web.Request, service_names: List[str]):
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
        services = self.compose.services

        if (
            add_context
            or since is not None
            or (until is not None and until < now)
            or tail is not None
        ):
            self.logger.debug(f"Providing log context since={since}, until={until}, tail={tail}")
            log_context = list(
                r
                for s in service_names
                for r in services[s].get_log_context(since=since, until=until, tail=tail)
            )
            log_context.sort(key=lambda x: x.created)
            for r in log_context:
                logs_to_response.emit(r)

        if follow or until is not None and until > now:
            for s in service_names:
                services[s].attach_log_handlers(logs_to_response, logs_to_response)

            def detach_loggers(fut):
                for s in service_names:
                    self.logger.debug(f"Detaching log sender from {s}")
                    services[s].detach_log_handlers(logs_to_response, logs_to_response)

            assert response.task is not None
            response.task.add_done_callback(detach_loggers)

        # Yield for context messages processing
        await asyncio.sleep(0)

        try:
            # TODO: cleaner solution? websocket here?
            while (
                (follow or (until is not None and datetime.datetime.now() < until))
                and not await back_stream.is_broken()
                and not all([services[s].terminated_and_done for s in service_names])
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

        await self.compose.stop_service(service, request_clean)

        return web.json_response("Stopped")

    async def post_restart_project(self, request: web.Request):
        self._get_project(request)
        body = await self._get_json_or_empty(request)
        services = body.get("services")

        await self.compose.restart_services(
            services, no_deps=body.get("no_deps", False), timeout=body.get("timeout", None)
        )

        return web.json_response("Restarted")

    async def post_restart_service(self, request: web.Request):
        self._get_project(request)
        service = self._get_service(request)
        body = await self._get_json_or_empty(request)

        await self.compose.restart_services(
            [service], no_deps=body.get("no_deps", False), timeout=body.get("timeout", None)
        )

        return web.json_response("Restarted")

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
            run_server(self.app, self.logger.getChild("http"), self.connection_path)
        )

    async def shutdown(self):
        if self.app is not None:
            await self.app.shutdown()


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
        if sys.platform == "win32":
            sites.append(NamedPipeSite(runner=runner, path=f"{path}", shutdown_timeout=20))
        else:
            sites.append(UnixSite(runner=runner, path=f"{path}", shutdown_timeout=20))

        for site in sites:
            await site.start()

        logger.debug("Server created")
        # Signal we create a server (might be unexpected)
        print("Server created", path, flush=True)

        delay = 10
        while True:
            logger.debug(f"Server sleep for {delay}")
            await asyncio.sleep(delay)
    finally:
        logger.debug("Cleanup after run_server")
        await runner.cleanup()
        # Seems to be cleaned on Windows
        if delete_pipe and sys.platform != "win32":
            os.unlink(f"{path}")
