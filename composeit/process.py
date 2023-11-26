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
from .utils import duration_to_seconds

import traceback


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

        self.build_time = None

        self.startup_semaphore = asyncio.Semaphore(0)
        self.process_initialization = None
        self.process: asyncio.subprocess.Process = None
        self.start_time = None
        self.stop_time = None
        self.watch_coro = None
        self.sleep_task = None

        self.restart_policy = service_config.get("restart", "no")
        assert self.restart_policy in RestartPolicyOptions

        self.restart_policy_config = {
            "delay": 5,
            "max_attempts": "infinite",
            "window": 0,
        }
        self.restart_policy_config.update(service_config.get("restart_policy", {}))

        self.color = color

        self.lout = logging.getLogger(f"{self.name}:")
        self.lerr = logging.getLogger(f"{self.name}>")
        self.log = logging.getLogger(f"{self.name}")

        logHandler = logging.StreamHandler(stream=sys.stderr)
        logHandler.setFormatter(logging.Formatter(" **%(name)s** %(message)s"))
        self.log.addHandler(logHandler)
        self.log.setLevel(logging.INFO)
        self.log.propagate = False

        streamHandler = logging.StreamHandler(stream=sys.stdout)
        streamHandler.setFormatter(self.get_output_formatter())
        self.lout.setLevel(logging.INFO)
        self.lout.addHandler(streamHandler)
        self.lout.propagate = False
        self.lerr.setLevel(logging.INFO)
        self.lerr.addHandler(streamHandler)
        self.lerr.propagate = False

        self.popen_kw = {}

        self.rc = None
        self.restarting = False
        self.terminated = False
        self.stopped = True
        self.exception = None

    def get_output_formatter(self):
        color_wrap = make_colored(self.color) if self.color is not None else not_colored
        return logging.Formatter(color_wrap("%(name)s %(message)s"))

    def request_clean(self):
        self.execute_clean = True

    def request_build(self):
        self.execute_build = True

    def get_state(self):
        if self.terminated:
            return "terminated"
        elif self.restarting:
            return "restarting"
        elif self.stopped:
            return "stopped"
        else:
            return "started"

    def attach_log_handler(self, handler: logging.StreamHandler):
        self.log.debug(f"Logger attached {handler}")
        self.log.addHandler(handler)
        self.lout.addHandler(handler)
        self.lerr.addHandler(handler)

    def detach_log_handler(self, handler: logging.StreamHandler):
        self.log.debug(f"Logger detached {handler}")
        self.log.removeHandler(handler)
        self.lout.removeHandler(handler)
        self.lerr.removeHandler(handler)

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
                to_add = {
                    k: v if v is not None else os.environ.get(k, "")
                    for d in entries
                    for k, v in d.items()
                }
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
                *command,
                stdin=asyncio.subprocess.PIPE,
                stderr=stream_mode,
                stdout=stream_mode,
                **popen_kw,
            )
        self.start_time = time.time()
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
                self.lerr.info(l.decode(errors="ignore").rstrip("\r\n"))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stderr line: {ex}")

    async def watch_stdout(self, process: asyncio.subprocess.Process):
        if process.stdout is None:
            return
        async for l in process.stdout:
            try:
                self.lout.info(l.decode(errors="ignore").rstrip("\r\n"))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stdout line: {ex}")

    async def wait_for_code(self):
        self.rc = await self.process.wait()
        self.stop_time = time.time()

    async def _resolve_restart(self):
        self.restarting = True

        max_attempts = self.restart_policy_config["max_attempts"]
        attempt = 1
        self.log.debug(
            f"Resolving restart for {self.name}, attempt={attempt}, terminated={self.terminated}, stopped={self.stopped}"
        )

        while max_attempts == "infinite" or attempt < max_attempts:
            if self.terminated:
                return False

            if self.restart_policy in ["always", "unless-stopped"] and not self.stopped:
                restarted = await self._restart_process()
            elif self.restart_policy == "on-failure" and self.rc != 0:
                restarted = await self._restart_process()
            else:
                return False

            # Most likely terminate() happened
            if not restarted:
                continue

            await self.started()
            success_after = duration_to_seconds(self.restart_policy_config["window"])
            if success_after <= 0:
                return True

            try:
                self.log.debug(f"Verifying successful startup for {success_after} seconds")
                # Prevent cancelation
                shielded_watch = asyncio.shield(self.watch_coro)
                await asyncio.wait_for(shielded_watch, success_after)
            except asyncio.exceptions.TimeoutError:
                # Success, it did not finish in the requested time
                self.log.debug(f"Restart successful")
                return True

            attempt += 1

        self.log.error(f"Restarting gave up after {max_attempts} attempts")
        return False

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
                    await self._start_process()
                    await self.started()
                    process_started = True

                while process_started:
                    await self.watch_coro
                    self.log.info(
                        f"{self.service_config['command']} finished with error code {self.rc}"
                    )

                    self.log.info(f"Restart policy is {self.restart_policy}")
                    process_started = await self._resolve_restart()
                    self.restarting = False
            except FileNotFoundError as ex:
                self.log.error(f"Error running command {self.name} {ex}")
                self.exception = ex
                break
            except Exception as ex:
                traceback.print_exc()
                self.log.error(f"Exception '{ex}'")
                raise
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
                if rc == 0:
                    self.build_time = time.time()
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

    async def _start_process(self):
        self.rc = None
        self.process = None
        self.start_time = None
        self.stop_time = None
        self.process_initialization = self._make_process()

    def _cancel_sleep(self):
        if self.sleep_task is not None:
            self.sleep_task.cancel()

    async def _sleep(self, seconds):
        try:
            self.sleep_task = asyncio.create_task(asyncio.sleep(seconds))
            await self.sleep_task
        except asyncio.exceptions.CancelledError:
            pass

    async def _restart_process(self):
        sleep_time = self.restart_policy_config["delay"]
        self.log.debug(f"Waiting for {sleep_time} seconds before restarting")

        await self._sleep(duration_to_seconds(sleep_time))

        # Check after a long sleep
        if self.terminated:
            return False

        await self._start_process()
        return True

    def _make_watch_coro(self):
        self.watch_coro = asyncio.gather(
            self.wait_for_code(), self.watch_stderr(self.process), self.watch_stdout(self.process)
        )

    async def started(self):
        self.process = await self.process_initialization
        self._make_watch_coro()

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

        for _ in range(10):
            running = 0
            for child in children:
                if child.is_running():
                    running += 1
                    time.sleep(0)
            if running == 0:
                break

        # TODO wait for a while until children are running? Then terminate
        # will block the thread...

        for child in children:
            if child.is_running():
                self.log.warning(f"Terminating leftover child process {child}")
                child.terminate()

    def terminate(self):
        self.log.debug(f"Terminating, rc:{self.rc}, process:{self.process}")
        self.terminated = True
        self._cancel_sleep()
        if self.rc is None and self.process is not None:
            self.log.debug("Sending terminate signal for terminate")
            self._terminate()
        self.startup_semaphore.release()
