import io
from time import strftime, localtime
import datetime
import traceback
import re
import collections.abc
from typing import Mapping, Dict, Union, Optional

from .dotenv.main import resolve_variables


def make_int(s: Optional[str]):
    return int(s) if s else None


def make_date(s: Optional[str]):
    return datetime.datetime.fromisoformat(s) if s else None


def get_stack_string():
    buffer = io.StringIO()
    traceback.print_exc(file=buffer)
    return buffer.getvalue()


def date_or_duration(text: str) -> datetime.datetime:
    try:
        return datetime.datetime.fromisoformat(text)
    except:
        pass
    return datetime.datetime.fromtimestamp(
        datetime.datetime.now().timestamp() + duration_to_seconds(text)
    )


def duration_to_seconds(duration: Union[str, int, float]) -> float:
    if isinstance(duration, (int, float)):
        return duration

    input = f"{duration}".lower().strip()
    negative = input[:1] == "-"
    if negative:
        input = input[1:]

    duration_re = re.compile(
        r"(\s*(?P<count>\d+(\.\d+)?)(?P<unit>(d|h|m|s|day|days|hour|hours|minute|minutes|second|seconds)?))"
    )

    result = 0.0
    multiplier = {"s": 1, "m": 60, "h": 60 * 60, "d": 60 * 60 * 24}

    for match in duration_re.findall(input):
        result += multiplier[(match[3] or "s")[0]] * float(match[1])

    return -result if negative else result


def duration_text(seconds):
    sign = ""
    if seconds < 0:
        sign = "-"
        seconds = -seconds
    D = 3600 * 24
    H = 3600
    M = 60

    s = round(seconds)
    d = s // D
    s -= d * D
    h = s // H
    s -= h * H
    m = s // M
    s -= m * M

    r = []
    if d > 0:
        r.append(f"{d}d")
    if h > 0:
        r.append(f"{h}h")
    if m > 0:
        r.append(f"{m}m")
    if s > 0:
        r.append(f"{s}s")

    if len(r) == 0:
        return sign + f"{round(seconds, 2)}s"
    else:
        return sign + " ".join(r)


def date_time_text(seconds):
    lt = localtime(seconds)
    dt = datetime.datetime.fromtimestamp(seconds)
    if datetime.date.today() == dt.date():
        return strftime("%H:%M:%S", lt)
    else:
        return strftime("%Y-%m-%d %H:%M:%S", lt)


def cumulative_time_text(seconds):
    D = 3600 * 24
    H = 3600
    M = 60

    s = round(seconds)
    d = s // D
    s -= d * D
    h = s // H
    s -= h * H
    m = s // M
    s -= m * M

    ds = f"{d}d" if d > 0 else ""
    if seconds > 1:
        return f"{ds}{h:02}{m:02}:{s:02}"
    else:
        return f"{round(seconds, 6)}"


def resolve_string(s: str) -> str:
    # NOTE: This is not fully compatible with https://docs.docker.com/compose/environment-variables/env-file/
    #       Required and alternative values are not handled. Single quote also a little different.
    #       We are working on that though B]
    #       https://github.com/Bajron/python-dotenv
    anti_variable = s.replace("$$", "<<dolar>>")

    # This requires handling equal signs inside?
    # expand = dotenv.dotenv_values(stream=io.StringIO(f'X="{anti_variable}"'))["X"]

    expand = resolve_variables([("H", f"{anti_variable}")], override=True)["H"]
    assert expand is not None
    return expand.replace("<<dolar>>", "$")

    # TODO: single quotes


def interpolate_variables(value):
    if isinstance(value, list):
        return [interpolate_variables(e) for e in value]
    if isinstance(value, dict):
        # TODO, is resolving key safe? For env, it is not handled in docker-compose
        return {interpolate_variables(k): interpolate_variables(v) for k, v in value.items()}
    if isinstance(value, str):
        return resolve_string(value)
    if isinstance(value, tuple):
        return tuple((interpolate_variables(v) for v in value))
    return value


# https://stackoverflow.com/questions/3232943/update-value-of-a-nested-dictionary-of-varying-depth
def update_dict(d: Dict, u: Mapping) -> Dict:
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update_dict(d.get(k, {}), v)
        else:
            d[k] = v
    return d
