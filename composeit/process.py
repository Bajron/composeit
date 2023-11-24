import logging
import asyncio
import psutil
import os
import sys
import subprocess
import time
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
    def __init__(
        self,
        sequence: int,
        name: str,
        service_config: dict,
        color: str,
        execute: bool = True,
        execute_build: bool = False,
        execute_clean: bool = False,
    ):
        self.sequence = sequence
        self.name = name
        self.service_config = service_config

        self.execute = execute
        self.execute_build = execute_build
        self.execute_clean = execute_clean

        self.startup_semaphore = asyncio.Semaphore(0)
        self.process_initialization = None
        self.process: asyncio.subprocess.Process = None

        self.restart_policy = service_config.get("restart", "no")
        assert self.restart_policy in RestartPolicyOptions

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
        self.stopped = True
        self.exception = None

    def request_clean(self):
        self.execute_clean = True

    def request_build(self):
        self.execute_build = True

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

        # TODO: Process started with shell kills cmd, but child persists...

        # NOTE: This is here to prevent sending CTRL_C_EVENT to children
        if os.name == "nt":
            popen_kw.update(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        # else: # TODO: not sure yet if required on Linux, could be helpful for closing children
        #     popen_kw.update(start_new_session=True)

        if service_config.get("shell", False):
            # TODO, injections?
            if "args" in service_config:
                print(" ** args ignored with a shell command")

            process = await asyncio.create_subprocess_shell(
                cmd=service_config["command"],
                stdin=asyncio.subprocess.PIPE,
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
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE, stderr=stream_mode, stdout=stream_mode, **popen_kw
            )
        return process

    def _output(self, sep: str, message: str):
        s = f"{self.name}{sep} {message}"
        if self.color is not None:
            s = colored(s, self.color)
        print(s, end="")

    async def watch_stderr(self, process: asyncio.subprocess.Process):
        if process.stderr is None:
            return
        async for l in process.stderr:
            try:
                self.lerr.info(l.decode(errors="ignore"))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stderr line: {ex}")

    async def watch_stdout(self, process: asyncio.subprocess.Process):
        if process.stdout is None:
            return
        async for l in process.stdout:
            try:
                self.lout.info(l.decode(errors="ignore"))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stdout line: {ex}")

    async def wait_for_code(self):
        self.rc = await self.process.wait()

    async def _resolve_restart(self):
        self.log.debug(f"Resolving restart for {self.name}, terminated={self.terminated}, stopped={self.stopped}")
        if self.terminated:
            return False

        if self.restart_policy in ["always", "unless-stopped"] and not self.stopped:
            await self._restart()
        elif self.restart_policy == "on-failure" and self.rc != 0:
            await self._restart()
        else:
            return False
        return True

    async def watch(self):
        await self.resolve_build()

        while self.execute and not self.terminated:
            # NOTE: We go back here after stop.
            #       All services can hang here by design
            self.log.debug("Awaiting start signal")
            await self.startup_semaphore.acquire()
            # While waiting for start, one can call build() or terminate()
            if self.terminated or not self.execute:
                return

            await self.resolve_build()

            try:
                process_started = False
                if self.execute:
                    await self._restart()
                    process_started = True

                while process_started:
                    await self.started()
                    await asyncio.gather(
                        self.wait_for_code(), self.watch_stderr(self.process), self.watch_stdout(self.process)
                    )
                    self.log.info(f"{self.service_config['command']} finished with error code {self.rc}")

                    self.log.info(f"Restart policy is {self.restart_policy}")
                    process_started = await self._resolve_restart()
            except FileNotFoundError as ex:
                self.log.error(f"Error running command {self.name} {ex}")
                self.exception = ex
                break

            await self.resolve_clean()

        self.log.debug("Restart loop exited")
        await self.resolve_clean()

    async def resolve_build(self):
        if not self.execute_build:
            return 0

        build_rc = await self._build()
        if build_rc != 0:
            self.log("Build failed")
            self.execute = False
            self.execute_build = True
        else:
            # It is one time action, need to reschedule for repeat
            self.execute_build = False
        return build_rc

    async def _build(self):
        if "build" in self.service_config:
            b = self.service_config["build"]
            if "shell_sequence" in b:
                self.log.debug("Processing build sequence")
                for cmd in b["shell_sequence"]:
                    rc = await self.execute_command(cmd)
                    if rc != 0:
                        self.log.warning(f"Build sequence interrupted with error")
                        break
                return rc
        return 0

    async def resolve_clean(self):
        if not self.execute_clean:
            return
        rc = await self._clean()
        if rc != 0:
            self.log.warning("Cleanup failed")
            # No success, try again
            self.execute_clean = True
        else:
            self.execute_clean = False

    async def _clean(self):
        worst_rc = 0
        if "clean" in self.service_config:
            c = self.service_config["clean"]
            if "shell_sequence" in c:
                self.log.debug("Processing cleanup sequence")
                for cmd in c["shell_sequence"]:
                    rc = await self.execute_command(cmd)
                    if rc != 0 and worst_rc == 0:
                        worst_rc = rc
                        # We continue to cleanup as much as possible
        return worst_rc

    async def execute_command(self, cmd):
        # NOTE: this one should not be supressed by logging: none
        stream_mode = asyncio.subprocess.PIPE

        if isinstance(cmd, list):
            process = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stderr=stream_mode, stdout=stream_mode
            )
        else:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdin=asyncio.subprocess.PIPE,
                stderr=stream_mode,
                stdout=stream_mode,
            )

        await asyncio.gather(self.watch_stderr(process), self.watch_stdout(process))
        return await process.wait()

    def start(self, request_build=None):
        if self.stopped:
            if request_build != None:
                self.execute_build = request_build

            self.log.debug("Starting")
            self.stopped = False
            self.startup_semaphore.release()
            return True
        else:
            self.log.warning("Does not seem to be stopped")
            return False

    async def _restart(self):
        self.rc = None
        self.process = None
        self.process_initialization = self._make_process()

    async def started(self):
        self.process = await self.process_initialization

    async def stop(self, request_clean=None):
        if request_clean != None:
            self.request_clean = request_clean

        if self.stopped and self.request_clean:
            await self.resolve_clean()

        self.log.debug("Stopping")
        self.stopped = True
        if self.rc is None and self.process is not None:
            self.log.debug("Sending terminate signal for stop")
            self._terminate()

    def _terminate(self):
        try:
            children = psutil.Process(self.process.pid).children(recursive=True)
        except psutil.NoSuchProcess as ex:
            self.log.warning(f"Process already closed {ex}")
            children = []
        self.process.stdin.close()
        self.process.terminate()

        # Tries to fix the leftover processes on Windows
        # Could be different with process group on Linux
        for i in range(10):
            time.sleep(0)

        # TODO wait for a while until children are running? Then terminate
        # will block the thread...

        for child in children:
            if child.is_running():
                self.log.warning(f"Terminating leftover child process {child}")
                child.terminate()

    def terminate(self):
        self.log.debug(f"Terminating, rc:{self.rc}, process:{self.process}")
        self.terminated = True
        if self.rc is None and self.process is not None:
            self.log.debug("Sending terminate signal for terminate")
            self._terminate()
        self.startup_semaphore.release()
