import logging
import asyncio
import psutil
import os
import locale
import sys
import subprocess
import time
import dotenv
import io
from termcolor import colored
from .utils import duration_to_seconds, get_stack_string
from .log_utils import LogKeeper

from typing import List, Optional, Union, Dict, Any


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
        color: Optional[str],
        execute: bool = True,
        execute_build: bool = False,
        execute_clean: bool = False,
    ):
        self.sequence: int = sequence
        self.name: str = name
        self.service_config: dict = service_config

        self.execute: bool = execute
        self.execute_build: bool = execute_build
        self.execute_clean: bool = execute_clean

        self.build_time = None

        self.startup_semaphore = asyncio.Semaphore(0)
        self.process_initialization = None
        self.process: Optional[asyncio.subprocess.Process] = None
        self.command: Union[str, List[str]] = self._get_command()
        self.start_time = None
        self.stop_time = None
        self.watch_coro = None
        self.sleep_task = None

        self.preferred_encoding = locale.getpreferredencoding(False)

        # NOTE: YAML translates `no` to False
        self.restart_policy = service_config.get("restart", "no") or "no"
        assert (
            self.restart_policy in RestartPolicyOptions
        ), f"restart value must be in {RestartPolicyOptions}, recieved {self.restart_policy}"

        self.restart_policy_config: dict = {
            "delay": 5,
            "max_attempts": "infinite",
            "window": 0,
        }
        self.restart_policy_config.update(service_config.get("restart_policy", {}))

        self.color: Optional[str] = color

        self._lout: logging.Logger = logging.getLogger(f"{self.name}:")
        self._lerr: logging.Logger = logging.getLogger(f"{self.name}>")
        # NOTE: avoid simply `name` because of side effects with "root" that returns the root logger
        self._log: logging.Logger = logging.getLogger(f"{self.name}*")

        self.lout: logging.LoggerAdapter = self._adapt_logger(self._lout)
        self.lerr: logging.LoggerAdapter = self._adapt_logger(self._lerr)
        self.log: logging.LoggerAdapter = self._adapt_logger(self._log)

        self.lout_keeper = LogKeeper()
        self.lerr_keeper = LogKeeper()
        self.log_keeper = LogKeeper()

        logHandler = logging.StreamHandler(stream=sys.stderr)
        logHandler.setFormatter(logging.Formatter(" **%(name)s* %(message)s"))
        self._log.addHandler(logHandler)
        self._log.addHandler(self.log_keeper)
        self._log.setLevel(logging.INFO)
        self._log.propagate = False

        streamHandler = logging.StreamHandler(stream=sys.stdout)
        streamHandler.setFormatter(self.get_output_formatter())
        self._lout.setLevel(logging.INFO)
        self._lout.addHandler(streamHandler)
        self._lout.addHandler(self.lout_keeper)
        self._lout.propagate = False
        self._lerr.setLevel(logging.INFO)
        self._lerr.addHandler(streamHandler)
        self._lerr.addHandler(self.lerr_keeper)
        self._lerr.propagate = False

        self.popen_kw: dict = {}

        self.rc: Optional[int] = None
        self.restarting: bool = False
        self.terminated: bool = False
        self.stopped: bool = True
        self.exception = None

    def _adapt_logger(self, logger: logging.Logger) -> logging.LoggerAdapter:
        return logging.LoggerAdapter(
            logger,
            extra={"color": self.color},
        )

    def get_output_formatter(self):
        color_wrap = make_colored(self.color) if self.color is not None else not_colored
        return logging.Formatter(color_wrap("%(name)s %(message)s"))

    def request_clean(self):
        self.execute_clean = True

    def request_build(self):
        self.execute_build = True

    def get_processes_info(self):
        def serialize_process(p: psutil.Process):
            row: Dict[str, Any] = {}
            row["username"] = p.username()
            row["pid"] = p.pid
            row["ppid"] = p.ppid()
            row["cpu_percent"] = p.cpu_percent()
            row["create_time"] = p.create_time()
            row["terminal"] = p.terminal() if hasattr(p, "terminal") else "--"
            cpu_times = p.cpu_times()
            row["cpu_times"] = {"user": cpu_times.user, "system": cpu_times.system}
            row["cmdline"] = p.cmdline()
            return row

        processes = []
        if self.rc is None:
            try:
                p = psutil.Process(self.process.pid)
                processes.append(serialize_process(p))
                for ch in p.children(recursive=True):
                    processes.append(serialize_process(ch))
            except psutil.NoSuchProcess:
                pass
        return processes

    def get_state(self):
        if self.terminated:
            return "terminated"
        elif self.restarting:
            return "restarting"
        elif self.stopped:
            return "stopped"
        else:
            return "started"

    def attach_log_handler(self, handler: logging.StreamHandler, provide_context: bool = False):
        if provide_context:
            c = self.lerr_keeper.window + self.lout_keeper.window + self.log_keeper.window
            c.sort(key=lambda x: x.created)
            for r in c:
                handler.emit(r)

        self.log.debug(f"Logger attached {handler}")
        self._log.addHandler(handler)
        self._lout.addHandler(handler)
        self._lerr.addHandler(handler)

    def detach_log_handler(self, handler: logging.StreamHandler):
        self.log.debug(f"Logger detached {handler}")
        self._log.removeHandler(handler)
        self._lout.removeHandler(handler)
        self._lerr.removeHandler(handler)

    def _get_command(self) -> Union[str, List[str]]:
        config = self.service_config
        if config.get("shell", False):
            # TODO, injections?
            if "args" in config:
                self.log.warning("args ignored with a shell command")
            command = config["command"]
            if not isinstance(command, str):
                raise TypeError("Expected string for a command in shell mode")
            return command
        else:
            command = (
                config["command"] if isinstance(config["command"], list) else [config["command"]]
            )
            command.extend(config.get("args", []))
            # We can get ints from YAML parsing here, so make everything a string
            # TODO: is it a problem if we get recursive list here or a map even?
            return [f"{c}" for c in command]

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

                def make(e):
                    try:
                        # Strings are already interpolated during resolve phase
                        return dotenv.dotenv_values(stream=io.StringIO(e), interpolate=False)
                    except Exception as ex:
                        self.log.warning(f"Error parsing environment element ({e}): {ex}")
                        return {}

                entries = [make(e) for e in env_definition]
                to_add = {
                    k: v if v is not None else os.environ.get(k, "")
                    for d in entries
                    for k, v in d.items()
                }
            if isinstance(env_definition, dict):
                to_add = {k: str(v) for k, v in env_definition.items()}
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

        self.preferred_encoding = locale.getpreferredencoding(False)

        self.command = self._get_command()
        if service_config.get("shell", False):
            process = await asyncio.create_subprocess_shell(
                cmd=self.command,
                stdin=asyncio.subprocess.PIPE,
                stderr=stream_mode,
                stdout=stream_mode,
                **popen_kw,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *self.command,
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
                self.lerr.info(self._handle_output_line(l))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stderr line: {ex}")

    async def watch_stdout(self, process: asyncio.subprocess.Process):
        if process.stdout is None:
            return
        async for l in process.stdout:
            try:
                self.lout.info(self._handle_output_line(l))
            except UnicodeDecodeError as ex:
                self.log.warning(f"Cannot decode stdout line: {ex}")

    def _handle_output_line(self, l: bytes):
        decoded_line = l.decode(encoding=self.preferred_encoding, errors="replace").rstrip("\r\n")
        # In case output is not capable of unicode
        decoded_line = decoded_line.replace("\ufffd", "?")
        # No terminal shenanigans
        ascii_escaped = decoded_line.replace("\u001b", "\\u001b")
        return ascii_escaped

    async def wait_for_code(self):
        self.rc = await self.process.wait()
        self.stop_time = time.time()

    async def _resolve_restart(self):
        self.restarting = True

        max_attempts = self.restart_policy_config["max_attempts"]
        attempt = 0
        self.log.debug(
            f"Resolving restart for {self.name}, attempt={attempt}, terminated={self.terminated}, stopped={self.stopped}"
        )

        while max_attempts == "infinite" or attempt < max_attempts:
            if self.terminated:
                return False

            if self.restart_policy in ["always", "unless-stopped"] and not self.stopped:
                restarted = await self._restart_process()
            elif self.restart_policy == "on-failure" and self.rc != 0 and not self.stopped:
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
                break

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
                    self.stopped = not process_started
                    self.restarting = False
            except FileNotFoundError as ex:
                self.log.error(f"Error running command {self.name} {ex}")
                self.exception = ex
                break
            except Exception as ex:
                self.log.debug(get_stack_string())
                self.log.error(f"Exception '{ex}'")
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

        self.log.debug(f"Executing {cmd}")
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

    def start(self, request_build: Optional[bool] = None):
        if self.stopped:
            if request_build is not None:
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
        if self.terminated or self.stopped:
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

    async def stop(self, request_clean: Optional[bool] = None):
        if request_clean is not None:
            self.execute_clean = request_clean

        if self.stopped and self.execute_clean:
            await self.resolve_clean()

        self.log.debug("Stopping")
        self.stopped = True
        self._cancel_sleep()
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
                try:
                    self.log.warning(f"Terminating leftover child process {child}")
                    child.terminate()
                except psutil.NoSuchProcess:
                    # Well ok, maybe we are too late
                    pass

    def terminate(self):
        self.log.debug(f"Terminating, rc:{self.rc}, process:{self.process}")
        self.terminated = True
        self._cancel_sleep()
        if self.rc is None and self.process is not None:
            self.log.debug("Sending terminate signal for terminate")
            self._terminate()
        self.startup_semaphore.release()
