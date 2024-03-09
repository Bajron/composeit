import argparse
import pathlib
import os
import sys
import platform
import logging
import dotenv
import asyncio
import subprocess
import json
import importlib.metadata

from typing import Dict, Any

from .compose import Compose
from .process import PossibleStates
from .utils import get_stack_string, date_or_duration
from .service_config import get_dict_from_env_list, get_signal, get_default_kill


def show_version(format="simple", short=False):
    v: Dict = {}
    v["composeit"] = importlib.metadata.version("composeit")
    if not short:
        v["system"] = platform.platform()
        v["python"] = {"version": sys.version, "platform": sys.platform, "os": os.name}

    if format == "json":
        json.dump(v, fp=sys.stdout, indent=2)
    else:
        if short:
            print(v["composeit"])
        else:
            print(f"composeit: {v['composeit']}")
            print(f"system: {v['system']}")
            print(f"python: {v['python']['version']}")


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
        "--build-arg",
        nargs="*",
        metavar="<key>=<value>",
        help="Environment variable to set during the build",
    )
    parser_up.add_argument("service", nargs="*", help="Specific service to start")
    parser_up.add_argument(
        "--no-start",
        default=False,
        action="store_true",
        help="Do not start services. Daemon stays started.",
    )
    parser_up.add_argument(
        "--detach",
        "-d",
        default=False,
        action="store_true",
        help="Start the server in the background",
    )
    parser_up.add_argument(
        "--abort-on-service-exit",
        "--abort-on-container-exit",
        default=False,
        action="store_true",
        help="Stop the project if any service exits (interactive only)",
    )
    parser_up.add_argument(
        "--exit-code-from",
        default=None,
        help="Return the exit code of the selected service",
    )
    parser_up.add_argument(
        "--attach", default=None, nargs="+", help="Attach only to the listed services"
    )
    parser_up.add_argument(
        "--attach-dependencies",
        default=False,
        action="store_true",
        help="Attach to dependencies logs",
    )
    parser_up.add_argument(
        "--no-attach",
        default=None,
        nargs="*",
        help="Hide logs from certain services",
    )
    parser_up.add_argument("--no-build", default=False, action="store_true", help="Do not build")
    parser_up.add_argument(
        "--no-deps",
        default=False,
        action="store_true",
        help="Do not start dependent services",
    )
    parser_up.add_argument(
        "--no-log-prefix",
        default=False,
        action="store_true",
        help="Do not print prefix in logs",
    )
    parser_up.add_argument(
        "--timestamps",
        default=False,
        action="store_true",
        help="Add timestamps to the logs",
    )
    parser_up.add_argument(
        "--no-color",
        dest="logs_no_color",
        default=False,
        action="store_true",
        help="Do not use color in the output",
    )
    parser_up.add_argument(
        "--timeout",
        "-t",
        type=float,
        default=10.0,
        help="Shutdown timeout for services",
    )
    parser_up.add_argument(
        "--wait",
        default=False,
        action="store_true",
        help="Wait for the services to be up/healthy. Implies detached mode.",
    )
    parser_up.add_argument(
        "--wait-timeout",
        default=None,
        type=float,
        help="Maximal wait time for services",
    )

    # --wait-timeout makes no sense, but requires also the following value filtering
    not_for_detached = ["-d", "--detach", "--abort-on-container-exit", "--wait"]

    parser_start = subparsers.add_parser("start", help="Startup the services")
    parser_start.add_argument("service", nargs="*", help="Specific service to start")
    parser_start.add_argument(
        "--no-start",
        default=False,
        action="store_true",
        help="Do not start services. Daemon stays started.",
    )
    parser_start.add_argument(
        "--no-deps", default=False, action="store_true", help="Do not start dependent services"
    )

    parser_build = subparsers.add_parser("build", help="Build the services")
    parser_build.add_argument(
        "--build-arg",
        nargs="*",
        metavar="<key>=<value>",
        help="Environment variable to set during the build",
    )
    parser_build.add_argument("service", nargs="*", help="Specific service to build")

    parser_build = subparsers.add_parser("images", help="Lookup the process executables")
    parser_build.add_argument("service", nargs="*", help="Specific service to inspect")

    parser_down = subparsers.add_parser("down", help="Close and cleanup the services")
    parser_down.add_argument("service", nargs="*", help="Specific service to cleanup")
    parser_down.add_argument("--timeout", "-t", type=float, help="Timeout for shutdown in seconds")

    parser_stop = subparsers.add_parser("stop", help="Close the services")
    parser_stop.add_argument("service", nargs="*", help="Specific service to close")
    parser_stop.add_argument("--timeout", "-t", type=float, help="Timeout for shutdown in seconds")

    parser_kill = subparsers.add_parser("kill", help="Send a signal to services")
    parser_kill.add_argument("service", nargs="*", help="Specific service to send the signal to")
    parser_kill.add_argument(
        "--signal",
        "-s",
        type=get_signal,
        default=get_default_kill(),
        help="Signal to send",
    )

    parser_logs = subparsers.add_parser("logs", help="Show logs from the services")
    parser_logs.add_argument("service", nargs="*", help="Specific services to show logs from")
    parser_logs.add_argument(
        "--with-context", default=None, action="store_true", help="Show previous logs context"
    )
    parser_logs.add_argument(
        "--follow", "-f", default=False, action="store_true", help="Follow the logs"
    )
    parser_logs.add_argument(
        "--no-context",
        dest="with_context",
        action="store_false",
        help="Do not show previous logs context",
    )
    parser_logs.add_argument(
        "--since", default=None, type=date_or_duration, help="Only show logs since date"
    )
    parser_logs.add_argument(
        "--until", default=None, type=date_or_duration, help="Only show logs until date"
    )
    parser_logs.add_argument(
        "--tail",
        "-n",
        type=int,
        default=None,
        help="Number of recent logs to show from each container",
    )
    parser_logs.add_argument(
        "--no-color",
        dest="logs_no_color",
        default=None,
        action="store_true",
        help="Monochrome output",
    )
    parser_logs.add_argument(
        "--no-log-prefix", default=None, action="store_true", help="Do not show service name"
    )
    parser_logs.add_argument(
        "--timestamps", "-t", default=None, action="store_true", help="Show timestamps"
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
    parser_ps.add_argument(
        "--format",
        default="default",
        choices=["json", "default"],
        help="Change output format (unstable)",
    )
    parser_ps.add_argument(
        "--no-trunc", default=False, action="store_true", help="Do not truncate output"
    )
    parser_ps.add_argument(
        "--quiet", "-q", default=False, action="store_true", help="Show service names only"
    )
    parser_ps.add_argument(
        "--status", default=None, choices=PossibleStates, help="Filter by status"
    )

    parser_restart = subparsers.add_parser("restart", help="Restart services")
    parser_restart.add_argument("service", nargs="*", help="Specific services to restart")
    parser_restart.add_argument(
        "--no-deps", default=False, action="store_true", help="Do not restart dependencies"
    )
    parser_restart.add_argument(
        "--timeout", "-t", default=None, type=float, help="Shutdown timeout for a service"
    )

    parser_top = subparsers.add_parser("top", help="Show processes")
    parser_top.add_argument("service", nargs="*", help="Specific services to show processes from")

    parser_config = subparsers.add_parser("config", help="Show services config")
    parser_config.add_argument("service", nargs="*", help="Specific services to show config of")
    parser_config.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Output format",
    )
    parser_config.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=None,
        help="Output file path (default is standard output)",
    )
    parser_config.add_argument(
        "-q",
        "--quiet",
        default=False,
        action="store_true",
        help="Do not output anything, just validate",
    )
    parser_config.add_argument(
        "--services",
        default=False,
        action="store_true",
        help="Print services list, one per line",
    )
    parser_config.add_argument(
        "--no-consistency",
        default=False,
        action="store_true",
        help="Disables consistency checks",
    )
    parser_config.add_argument(
        "--no-interpolate",
        default=False,
        action="store_true",
        help="Disables environment variables expansion",
    )
    parser_config.add_argument(
        "--no-normalize",
        default=False,
        action="store_true",
        help="Disables format normalization",
    )
    parser_config.add_argument(
        "--no-path-resolution",
        default=False,
        action="store_true",
        help="Disables path resolution",
    )

    parser_version = subparsers.add_parser("version", help="Show version")
    parser_version.add_argument(
        "--format",
        "-f",
        default="pretty",
        choices=["pretty", "json"],
        help="Format output",
    )
    parser_version.add_argument(
        "--short", default=False, action="store_true", help="Show only composeit version"
    )

    parser_wait = subparsers.add_parser("wait", help="Block until the first service stops")
    parser_wait.add_argument("service", nargs="*", help="Specific services to wait for")
    parser_wait.add_argument(
        "--down-project",
        default=False,
        action="store_true",
        help="Down project when service stops",
    )

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
    original_working_directory = os.getcwd()
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

        defer_config_load = options.command in [
            "ps",
            "top",
            "logs",
            "attach",
            "stop",
            "down",
            "config",
            "version",
        ]

        if options.command == "version":
            show_version(options.format, options.short)
            return 0

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
                if options.detach or options.wait:
                    without_detach = lambda x: x not in not_for_detached
                    up = sys.argv.index("up")
                    filtered_argv = sys.argv[:up] + list(filter(without_detach, sys.argv[up:]))
                    if "--wait-timeout" in filtered_argv:
                        wt = filtered_argv.index("--wait-timeout")
                        filtered_argv = filtered_argv[:wt] + filtered_argv[wt + 2 :]

                    popen_kw: Dict[str, Any] = {}
                    if os.name == "nt":
                        # subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
                        # FIXME: closing parent still takes the process down...
                        popen_kw.update(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                    else:
                        popen_kw.update(start_new_session=True)
                    subprocess.Popen(
                        filtered_argv,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=original_working_directory,
                        **popen_kw,
                    )

                    if options.wait:
                        try:
                            asyncio.run(
                                asyncio.wait_for(
                                    compose.wait_for_up(services=services),
                                    timeout=options.wait_timeout,
                                )
                            )
                            return 0
                        except asyncio.TimeoutError:
                            return 1

                    return 0
                else:
                    compose.shutdown_timeout = options.timeout
                    return asyncio.run(
                        compose.up(
                            services,
                            no_build=options.no_build,
                            abort_on_exit=options.abort_on_service_exit,
                            code_from=options.exit_code_from,
                            attach=options.attach,
                            attach_dependencies=options.attach_dependencies,
                            no_attach=options.no_attach,
                            no_deps=options.no_deps,
                            no_log_prefix=options.no_log_prefix,
                            no_color=options.logs_no_color,
                            timestamps=options.timestamps,
                        )
                    )
            elif options.command == "start":
                return asyncio.run(compose.start(services, no_deps=options.no_deps))
            elif options.command == "build":
                return asyncio.run(compose.build(services))
            elif options.command == "images":
                return asyncio.run(compose.images(services))
            elif options.command == "down":
                timeout = options.timeout if hasattr(options, "timeout") else None
                return asyncio.run(compose.down(services, timeout=timeout))
            elif options.command == "stop":
                timeout = options.timeout if hasattr(options, "timeout") else None
                return asyncio.run(compose.stop(services, timeout=timeout))
            elif options.command == "restart":
                return asyncio.run(
                    compose.restart(services, no_deps=options.no_deps, timeout=options.timeout)
                )
            elif options.command == "kill":
                return asyncio.run(compose.kill(services, signal=options.signal))
            elif options.command == "logs":
                return asyncio.run(
                    compose.logs(
                        services,
                        context=options.with_context,
                        follow=options.follow,
                        since=options.since,
                        until=options.until,
                        tail=options.tail,
                        color=None if options.logs_no_color is None else not options.logs_no_color,
                        timestamps=options.timestamps,
                        prefix=None if options.no_log_prefix is None else not options.no_log_prefix,
                    )
                )
            elif options.command == "attach":
                assert services is not None and len(services) == 1
                return asyncio.run(compose.attach(services[0], options.with_context))
            elif options.command == "config":
                return process_config_option(compose, options)
            elif options.command == "ps":
                return asyncio.run(
                    compose.ps(
                        services,
                        format=options.format,
                        truncate=not options.no_trunc,
                        quiet=options.quiet,
                        status=options.status,
                    )
                )
            elif options.command == "top":
                return asyncio.run(compose.top(services))
            elif options.command == "wait":
                return asyncio.run(compose.wait(services, down_project=options.down_project))
            else:
                cfg_log.error(f"Unhandled option {options.command}")
                return 10
    except FileNotFoundError as ex:
        cfg_log.debug(get_stack_string())
        cfg_log.error(f"File not found {ex.filename}")
        return -1
    else:
        parser.print_help()
        return 1


import yaml
import json


def process_config_option(compose: Compose, options):
    assert compose.service_config is None

    compose.assure_service_config(
        check_consistency=not options.no_consistency,
        interpolate=not options.no_interpolate,
        normalize=not options.no_normalize,
        resolve_paths=not options.no_path_resolution,
    )

    assert compose.service_config is not None

    if hasattr(options, "service") and len(options.service) > 0:
        compose.service_config["services"] = dict(
            filter(lambda kv: kv[0] in options.service, compose.service_config["services"].items())
        )

    if options.quiet:
        return

    with (
        open(options.output, "w")
        if options.output is not None
        else open(sys.stdout.fileno(), "w", closefd=False)
    ) as stream:
        if options.services:
            for s in compose.service_config["services"].keys():
                print(s, file=stream)
            return

        if options.format == "json":
            json.dump(compose.service_config, fp=stream, indent=2)
        elif options.format == "yaml":
            yaml.dump(compose.service_config, stream=stream)
