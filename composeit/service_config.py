import signal
import logging
import os
import dotenv
import io
from .utils import update_dict
from typing import Union, List, Optional, Dict


def get_stop_signal(config: dict) -> signal.Signals:
    s = config.get("stop_signal", signal.SIGTERM)
    if isinstance(s, int):
        s = signal.Signals(s)
    if isinstance(s, str):
        if hasattr(signal.Signals, s):
            s = getattr(signal.Signals, s)
        else:
            raise ValueError(f"Unknown signal {s}")
    return s


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
    service_config: dict, logger: Optional[logging.Logger] = None
) -> Union[str, List[str]]:
    config = service_config
    if config.get("shell", False):
        # TODO, injections?
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


def get_environemnt(
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
