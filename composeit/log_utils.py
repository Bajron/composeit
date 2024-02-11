import logging
import json
import datetime
from logging import LogRecord
from typing import Dict, List, Mapping, Any, Literal, Optional, Union
from termcolor import colored


class LogKeeper(logging.Handler):
    def __init__(
        self,
        window: int = 10,
        level: Union[int, str] = 0,
    ) -> None:
        super().__init__(level)
        self.window_size: int = window
        self.window: List[LogRecord] = []

    def emit(self, record: LogRecord) -> None:
        self.window.append(record)
        if len(self.window) > self.window_size:
            self.window.pop(0)


class JsonFormatter(logging.Formatter):
    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: Literal["%", "{", "$"] = "%",
        validate: bool = True,
    ) -> None:
        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        json_object = {}
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)

        json_object["name"] = record.name
        json_object["levelno"] = record.levelno
        json_object["create_time"] = record.created
        json_object["message"] = self.formatMessage(record)

        if hasattr(record, "color"):
            json_object["color"] = record.color

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            json_object["exception"] = record.exc_text
        if record.stack_info:
            json_object["stack"] = self.formatStack(record.stack_info)
        return json.dumps(json_object)


def build_print_function(single, color=None, prefix=None, timestamps=None):
    default_color = True
    default_prefix = True
    default_timestamps = False
    if single:
        default_prefix = False
        default_color = False

    if color is None:
        color = default_color
    if prefix is None:
        prefix = default_prefix
    if timestamps is None:
        timestamps = default_timestamps

    keys = []
    if prefix:
        keys.append("name")
    keys.append("message")

    def print_function(fields: Dict[str, Any]):
        msg = " ".join([fields[k] for k in keys])
        if timestamps:
            ts = datetime.datetime.fromtimestamp(fields["create_time"]).isoformat()
            msg = f"{ts} {msg}"
        c = fields.get("color", None)
        if color and c is not None:
            print(colored(msg, c))
        else:
            print(msg)

    return print_function


def print_message(fields: Dict[str, str]):
    print(f"{fields['message']}")


def print_message_prefixed(fields: Dict[str, str]):
    print(f"{fields['name']} {fields['message']}")


def print_color_message_prefixed(fields: Dict[str, str]):
    c = fields.get("color", None)
    s = f"{fields['name']} {fields['message']}"
    print(colored(s, c) if c else s)
