import argparse
import pathlib
import os
import sys
import logging
import dotenv
import asyncio
import pprint

from typing import Dict

from .compose import Compose
from .utils import get_stack_string
from .service_config import get_dict_from_env_list


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Define and run applications",
    )
    cfg_log = logging.getLogger("config")
    parser.add_argument(
        "-f",
        "--file",
        default=[],
        action="append",
        type=pathlib.Path,
        help="Compose configuration file",
    )
    parser.add_argument("-p", "--project-name", default=None, type=str, help="Project name")
    parser.add_argument(
        "--project-directory", default=None, type=pathlib.Path, help="Alternate working directory"
    )
    parser.add_argument("--env-file", default=[], action="append", type=pathlib.Path)
    parser.add_argument("--verbose", default=False, action="store_true")
    parser.add_argument("--no-color", default=False, action="store_true")

    subparsers = parser.add_subparsers(dest="command", help="sub-command help")
    parser_up = subparsers.add_parser("up", help="Startup the services")
    parser_up.add_argument("--build", default=False, action="store_true", help="Rebuild services")
    parser_up.add_argument(
        "--build-arg", nargs="*", help="Environment variable to set during the build"
    )
    parser_up.add_argument("service", nargs="*", help="Specific service to start")
    parser_up.add_argument(
        "--no-start",
        default=False,
        action="store_true",
        help="Do not start services. Daemon stays started.",
    )

    parser_start = subparsers.add_parser("start", help="Startup the services")
    parser_start.add_argument("service", nargs="*", help="Specific service to start")
    parser_start.add_argument(
        "--no-start",
        default=False,
        action="store_true",
        help="Do not start services. Daemon stays started.",
    )

    parser_build = subparsers.add_parser("build", help="Build the services")
    parser_build.add_argument(
        "--build-arg", nargs="*", help="Environment variable to set during the build"
    )
    parser_build.add_argument("service", nargs="*", help="Specific service to build")

    parser_down = subparsers.add_parser("down", help="Close and cleanup the services")
    parser_down.add_argument("service", nargs="*", help="Specific service to close")

    parser_stop = subparsers.add_parser("stop", help="Close the services")
    parser_stop.add_argument("service", nargs="*", help="Specific service to close")

    parser_logs = subparsers.add_parser("logs", help="Show logs from the services")
    parser_logs.add_argument("service", nargs="*", help="Specific services to show logs from")
    parser_logs.add_argument(
        "--with-context", default=False, action="store_true", help="Show previous logs context"
    )
    parser_logs.add_argument(
        "--no-context",
        dest="with_context",
        action="store_false",
        help="Do not show previous logs context",
    )

    parser_attach = subparsers.add_parser("attach", help="Attach to a service")
    parser_attach.add_argument("service", nargs=1, help="Specific service to attach to")
    parser_attach.add_argument(
        "--with-context", default=False, action="store_true", help="Show previous logs context"
    )
    parser_attach.add_argument(
        "--no-context",
        dest="with_context",
        action="store_false",
        help="Do not show previous logs context",
    )

    parser_ps = subparsers.add_parser("ps", help="Show services state")
    parser_ps.add_argument("service", nargs="*", help="Specific services to show")

    parser_top = subparsers.add_parser("top", help="Show processes")
    parser_top.add_argument("service", nargs="*", help="Specific services to show processes from")

    parser_config = subparsers.add_parser("config", help="Show services config")

    options = parser.parse_args()

    if options.verbose:
        print(" ** Verbose **", file=sys.stderr)
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)-10s %(thread)7d %(levelname)7s %(filename)s:%(lineno)-4d %(message)s",
        )

        cfg_log.debug(f"Parsed options: {options}")
        cfg_log.debug(f"os.name = {os.name}")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    use_color = os.isatty(sys.stdout.fileno())
    cfg_log.debug(f"Colors by default {use_color}")
    # TODO: option to force color? the --ansi thing?
    if options.no_color:
        use_color = False
    if use_color and os.name == "nt":
        cfg_log.debug(f'Running `os.system("color")`')
        os.system("color")

    working_directory = pathlib.Path(os.getcwd())
    if options.project_directory:
        working_directory = options.project_directory
    elif len(options.file) > 0:
        working_directory = options.file[0].parent
    working_directory = working_directory.absolute()
    cfg_log.debug(f"Working directory: {working_directory}")

    file_choices = ["composeit.yml", "composeit.yaml"]
    if len(options.file) > 0:
        service_files = [f.absolute() for f in options.file]
    else:
        for f in file_choices:
            if (working_directory / f).exists():
                service_files = [working_directory / f]
                break
        else:
            service_files = [working_directory / file_choices[0]]
    cfg_log.debug(f"Service file: {service_files}")

    project_name = options.project_name if options.project_name else working_directory.name

    cfg_log.debug(f"Project name: {project_name}")

    env_files = [e.absolute() for e in options.env_file]

    # NOTE: evaluate all paths from the commandline before changing working directory
    os.chdir(working_directory)
    cfg_log.debug(f"Changed directory to: {working_directory}")

    if len(env_files) == 0 and pathlib.Path(".env").exists():
        env_files = [pathlib.Path(".env").absolute()]
    cfg_log.debug(f"Environment files: {env_files}")

    for env_file in env_files:
        if env_file.exists():
            cfg_log.debug(f"Reading environment file {env_file}")
            dotenv.load_dotenv(env_file, override=True, single_quotes_expand=False)
        else:
            print("Provided environment file does not exist", file=sys.stderr)
            return 1

    try:
        build_args: Dict[str, str] = {}
        if hasattr(options, "build_arg"):
            build_args = get_dict_from_env_list(
                options.build_arg or [], cfg_log.getChild("build_arg")
            )

        defer_config_load = options.command in ["ps", "top", "logs", "attach", "stop", "down"]
        compose = Compose(
            project_name,
            working_directory,
            service_files,
            verbose=options.verbose,
            use_color=use_color,
            defer_config_load=defer_config_load,
            build_args=build_args,
        )

        if hasattr(options, "command") and options.command is not None:
            services = None
            if hasattr(options, "service") and len(options.service) > 0:
                services = options.service

            if hasattr(options, "no_start") and options.no_start:
                services = []

            if options.command == "up":
                return asyncio.run(compose.up(services))
            elif options.command == "start":
                return asyncio.run(compose.start(services))
            elif options.command == "build":
                return asyncio.run(compose.build(services))
            elif options.command == "down":
                return asyncio.run(compose.down(services))
            elif options.command == "stop":
                return asyncio.run(compose.stop(services))
            elif options.command == "logs":
                return asyncio.run(compose.logs(services, options.with_context))
            elif options.command == "attach":
                assert services is not None and len(services) == 1
                return asyncio.run(compose.attach(services[0], options.with_context))
            elif options.command == "config":
                return pprint.pprint(compose.service_config)
            elif options.command == "ps":
                return asyncio.run(compose.ps(services))
            elif options.command == "top":
                return asyncio.run(compose.top(services))
            else:
                cfg_log.error(f"Unhandled option {options.command}")
                return 10
    except FileNotFoundError as ex:
        cfg_log.debug(get_stack_string())
        cfg_log.error(f"File not found {ex.filename}")
    else:
        parser.print_help()
        return 1
