import dotenv
import io
from time import strftime, localtime
import datetime
import traceback

def get_stack_string():
    buffer = io.StringIO()
    traceback.print_exc(file=buffer)
    return buffer.getvalue()

def duration_to_seconds(duration):
    if isinstance(duration, (int, float)):
        return duration
    # TODO
    raise Exception(f"Duration {duration} not handled. Only numbers supported for now")


def duration_text(seconds):
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
        return f"{round(seconds, 2)}s"
    else:
        return " ".join(r)


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


def resolve_string(s: str):
    # NOTE: This is not fully compatible with https://docs.docker.com/compose/environment-variables/env-file/
    #       Required and alternative values are not handled. Single quote also a little different.
    #       We are working on that though B]
    #       https://github.com/Bajron/python-dotenv
    anti_variable = s.replace("$$", "<<dolar>>")

    # This requires handling equal signs inside?
    #expand = dotenv.dotenv_values(stream=io.StringIO(f'X="{anti_variable}"'))["X"]

    expand = dotenv.main.resolve_variables([("H", f"{anti_variable}")], override=True)["H"]
    return expand.replace("<<dolar>>", "$")

    # TODO: single quotes


def resolve(value):
    if isinstance(value, list):
        return [resolve(e) for e in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, str):
        return resolve_string(value)
    return value
