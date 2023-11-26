import dotenv
import io


def duration_to_seconds(duration):
    if isinstance(duration, (int, float)):
        return duration
    # TODO
    raise Exception(f"Duration {duration} not handled. Only numbers supported for now")


def resolve_string(s: str):
    # NOTE: This is not fully compatible with https://docs.docker.com/compose/environment-variables/env-file/
    #       Required and alternative values are not handled. Single quote also a little different.
    #       We are working on that though B]
    #       https://github.com/Bajron/python-dotenv
    anti_variable = s.replace("$$", "<<dolar>>")
    expand = dotenv.dotenv_values(stream=io.StringIO(f'X="{anti_variable}"'))["X"]
    return expand.replace("<<dolar>>", "$")

    return dotenv.main.resolve_variables({"H": f"{s}"})["H"]
    # TODO: single quotes
    # TODO: new interface after change


def resolve(value):
    if isinstance(value, list):
        return [resolve(e) for e in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, str):
        return resolve_string(value)
    return value
