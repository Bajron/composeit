import aiohttp
import logging
import datetime
from typing import Optional, List, Dict


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
