import signal
import logging
import os
import shutil
import dotenv
import io
import yaml
from pathlib import Path
from .utils import update_dict
from typing import Union, List, Optional, Dict, overload


class ServiceFiles:
    def __init__(self, service_files: List[Path]) -> None:
        self.paths: List[Path] = service_files
        self.loaded_files: Optional[List[dict]] = None

    def get_project_name(self) -> Optional[str]:
        self._assure_loaded()
        assert self.loaded_files is not None
        for d in reversed(self.loaded_files):
            if d is not None and "name" in d:
                return d["name"]
        return None

    def get_parsed_files(self) -> List[dict]:
        self._assure_loaded()
        assert self.loaded_files is not None
        return self.loaded_files

    def _assure_loaded(self):
        if self.loaded_files is None:
            self._load_files()

    def _load_files(self):
        self.loaded_files = [yaml.load(file.open(), Loader=UniqueKeyLoader) for file in self.paths]


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


def get_stop_signal(config: dict) -> signal.Signals:
    s = config.get("stop_signal", signal.SIGTERM)
    return get_signal(s)


def get_signal(s: Union[int, str, signal.Signals]) -> signal.Signals:
    if isinstance(s, int):
        sig = signal.Signals(s)
    elif isinstance(s, str):
        if hasattr(signal.Signals, s):
            sig = getattr(signal.Signals, s)
        else:
            try:
                sig = signal.Signals(int(s))
            except:
                raise ValueError(f"Unknown signal {s}")
    else:
        sig = s
    return sig


def get_default_kill() -> signal.Signals:
    return get_signal("SIGTERM" if os.name == "nt" else "SIGKILL")


def get_stop_grace_period(config: dict) -> float:
    return config.get("stop_grace_period", 10)


def get_minimal() -> dict:
    return {"services": []}


def get_shared_logging_config(services_config: dict):
    shared_logging_config: Dict = {}
    for name, service_config in services_config["services"].items():
        if "logging" in service_config:
            l = service_config["logging"]
            driver = l.get("driver", "")

            if driver == "logging.config.dictConfig.shared":
                created_loggers = [f"{name}{c}" for c in ":>*"]
                config = l.get("config", {})
                config["loggers"] = {
                    l[0]: l[1] for l in config.get("loggers", {}).items() if l[0] in created_loggers
                }
                shared_logging_config = update_dict(shared_logging_config, config)
    return shared_logging_config


def get_command(
    service_config: dict, logger: Optional[Union[logging.Logger, logging.LoggerAdapter]] = None
) -> Union[str, List[str]]:
    """Gets command from a config. Shell command is always a string, regular call is always a list."""
    config = service_config
    if config.get("shell", False):
        if "args" in config and logger is not None:
            logger.warning("args ignored with a shell command")
        command = config["command"]
        if not isinstance(command, str):
            raise TypeError("Expected string for a command in shell mode")
        return command
    else:
        command = config["command"] if isinstance(config["command"], list) else [config["command"]]
        command.extend(config.get("args", []))
        # We can get ints from YAML parsing here, so make everything a string
        # TODO: is it a problem if we get recursive list here or a map even?
        return [f"{c}" for c in command]


@overload
def resolve_command(command: str) -> str: ...


@overload
def resolve_command(command: List[str]) -> List[str]: ...


def resolve_command(command: Union[str, List[str]]):
    if isinstance(command, list):
        # This way we have a predetermined application lookup
        # See the warning in https://docs.python.org/3/library/subprocess.html#subprocess.Popen
        # and https://docs.python.org/3/library/shutil.html#shutil.which

        command = command.copy()
        resolved = shutil.which(command[0])
        if resolved is not None:
            command[0] = resolved
    return command


def get_process_path(resolved_command: Union[str, List[str]]) -> str:
    if isinstance(resolved_command, str):
        # We could replicate Python behavior to find shell, but it may change for certain versions...
        # For example in 3.12 it changes for Windows.
        # It stays simply "shell" to signal its vagueness and discourage usage.
        return f"<shell>"
    elif isinstance(resolved_command, list):
        return resolved_command[0]

    assert False, "resolved command must be a list or str"
    return f"<unknown>"


def get_environment(
    config: dict, logger: Union[logging.Logger, logging.LoggerAdapter]
) -> Optional[Dict[str, str]]:
    """Extracts environment options from a dictionary

    Handles: "inherit_environment", "env_file", "environment"
    """
    if config.get("inherit_environment", True):
        env = None
    else:
        # TODO: minimal viable env
        if os.name == "nt":
            env = {"SystemRoot": os.environ.get("SystemRoot", "")}
        else:
            env = {}

    if "env_file" in config:
        ef = config["env_file"]
        if not isinstance(ef, list):
            ef = [ef]

        if env is None:
            env = os.environ.copy()

        for f in ef:
            # TODO: confirm behaviour of just names
            env.update(
                {
                    k: v if v is not None else os.environ.get(k, "")
                    for k, v in dotenv.dotenv_values(f).items()
                }
            )

    if "environment" in config:
        env_definition = config["environment"]
        if isinstance(env_definition, str):
            env_definition = [env_definition]

        if isinstance(env_definition, list):
            to_add = get_dict_from_env_list(env_definition, logger)
        if isinstance(env_definition, dict):
            to_add = {k: str(v) for k, v in env_definition.items()}

        if env is None:
            env = os.environ.copy()

        env.update(to_add)
    return env


def get_dict_from_env_list(
    value_list: List[str], logger: Union[logging.Logger, logging.LoggerAdapter]
) -> Dict[str, str]:
    def make(e):
        if isinstance(e, tuple):
            return {e[0]: e[1]}
        elif isinstance(e, str):
            try:
                # Strings are already interpolated during resolve phase
                return dotenv.dotenv_values(stream=io.StringIO(e), interpolate=False)
            except Exception as ex:
                logger.warning(f"Error parsing environment element ({e}): {ex}")
                return {}
        else:
            logger.warning(f"Unexpected type of {e}")
            return {}

    entries = [make(e) for e in value_list]
    return {k: v if v is not None else os.environ.get(k, "") for d in entries for k, v in d.items()}
