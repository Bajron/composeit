import logging
import asyncio
import os
import sys
import dotenv
import io
from termcolor import colored


def make_colored(color):
    def f(s):
        return colored(s, color)

    return f


def not_colored(s):
    return s


RestartPolicyOptions = ["no", "always", "on-failure", "unless-stopped"]


class AsyncProcess:
    def __init__(self, sequence: int, name: str, service_config: dict, color: str):
        self.sequence = sequence
        self.name = name
        self.service_config = service_config

        self.startup_semaphore = asyncio.Semaphore(0)
        self.process_initialization = None
        self.process: asyncio.subprocess.Process = None

        self.restart = service_config.get("restart", "no")
        assert self.restart in RestartPolicyOptions

        self.color = color

        self.lout = logging.getLogger(f"{self.name}: ")
        self.lerr = logging.getLogger(f"{self.name}> ")
        self.log = logging.getLogger(f"{self.name}")

        logHandler = logging.StreamHandler(stream=sys.stderr)
        logHandler.setFormatter(logging.Formatter(" **%(name)s** %(message)s"))
        self.log.addHandler(logHandler)
        self.log.setLevel(logging.INFO)
        self.log.propagate = False

        color_wrap = make_colored(self.color) if self.color is not None else not_colored

        formatter = logging.Formatter(color_wrap("%(name)s%(message)s"))
        streamHandler = logging.StreamHandler(stream=sys.stdout)
        streamHandler.setFormatter(formatter)
        streamHandler.terminator = ""
        self.lout.setLevel(logging.INFO)
        self.lout.addHandler(streamHandler)
        self.lout.propagate = False
        self.lerr.setLevel(logging.INFO)
        self.lerr.addHandler(streamHandler)
        self.lerr.propagate = False

        self.popen_kw = {}

        self.rc = None
        self.terminated = False
        self.stopped = False
        self.exception = None

    async def _make_process(self):
        service_config = self.service_config

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

        stream_mode = asyncio.subprocess.PIPE
        if "logging" in service_config:
            l = service_config["logging"]
            driver = l.get("driver", "")
            if driver == "none":
                stream_mode = asyncio.subprocess.DEVNULL

        self.popen_kw = popen_kw = {"env": env}

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
                stderr=stream_mode,
                stdout=stream_mode,
                **popen_kw,
            )
        else:
            command = (
                service_config["command"]
                if isinstance(service_config["command"], list)
                else [service_config["command"]]
            )
            command.extend(service_config.get("args", []))
            process = await asyncio.create_subprocess_exec(*command, stderr=stream_mode, stdout=stream_mode, **popen_kw)
        return process

    def _output(self, sep: str, message: str):
        s = f"{self.name}{sep} {message}"
        if self.color is not None:
            s = colored(s, self.color)
        print(s, end="")

    async def watch_stderr(self):
        if self.process.stderr is None:
            return
        async for l in self.process.stderr:
            self.lerr.info(l.decode())

    async def watch_stdout(self):
        if self.process.stdout is None:
            return
        async for l in self.process.stdout:
            self.lout.info(l.decode())

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
        while not self.stopped and not self.terminated:
            await self.startup_semaphore.acquire()
            if self.terminated:
                return

            await self._restart()
            while True:
                try:
                    await self.started()
                    await asyncio.gather(self.wait_for_code(), self.watch_stderr(), self.watch_stdout())
                    self.log.info(f"{self.service_config['command']} finished with error code {self.rc}")

                    self.log.info(f"Restart policy is {self.restart}")
                    if not await self._resolve_restart():
                        break
                except FileNotFoundError as ex:
                    self.log.error(f"Error running command {self.name} {ex}")
                    self.exception = ex
                    break

    def start(self):
        self.stopped = False
        self.startup_semaphore.release()

    async def _restart(self):
        self.rc = None
        self.process = None
        self.process_initialization = self._make_process()

    async def started(self):
        self.process = await self.process_initialization

    def stop(self):
        self.stopped = True
        if self.rc is None and self.process is not None:
            self.process.terminate()

    def terminate(self):
        self.terminated = True
        if self.rc is None and self.process is not None:
            self.process.terminate()
        self.startup_semaphore.release()
