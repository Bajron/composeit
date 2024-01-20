import signal
import logging
from .utils import update_dict
from typing import Union, List, Optional


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
    shared_logging_config = {}
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
