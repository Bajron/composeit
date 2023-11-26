import argparse
import pathlib
import os
import sys
import logging
import dotenv
import asyncio
import pprint


from .compose import Compose


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Define and run applications",
    )
    cfg_log = logging.getLogger("config")
    parser.add_argument(
        "-f", "--file", default=[], action="append", type=pathlib.Path, help="Compose configuration file"
    )
    parser.add_argument("-p", "--project-name", default=None, type=str, help="Project name")
    parser.add_argument("--project-directory", default=None, type=pathlib.Path, help="Alternate working directory")
    parser.add_argument("--env-file", default=[], action="append", type=pathlib.Path)
    parser.add_argument("--verbose", default=False, action="store_true")
    parser.add_argument("--no-color", default=False, action="store_true")

    parser.add_argument(
        "--test-server",
        default=None,
        metavar="<PATH>",
        help="Temporary option to perform GET with a provided URL on the server",
    )
    parser.add_argument(
        "--test-server-post",
        default=None,
        metavar="<PATH>",
        help="Temporary option to perform POST with a provided URL on the server",
    )

    subparsers = parser.add_subparsers(dest="command", help="sub-command help")
    parser_up = subparsers.add_parser("up", help="Startup the services")
    parser_up.add_argument("--build", default=False, action="store_true", help="Rebuild services")
    parser_up.add_argument("service", nargs="*", help="Specific service to start")

    parser_up = subparsers.add_parser("start", help="Startup the services")
    parser_up.add_argument("service", nargs="*", help="Specific service to start")

    parser_up = subparsers.add_parser("build", help="Build the services")
    parser_up.add_argument("service", nargs="*", help="Specific service to build")

    parser_down = subparsers.add_parser("down", help="Close and cleanup the services")
    parser_down.add_argument("service", nargs="*", help="Specific service to close")

    parser_stop = subparsers.add_parser("stop", help="Close the services")
    parser_stop.add_argument("service", nargs="*", help="Specific service to close")

    parser_logs = subparsers.add_parser("logs", help="Show logs from the services")
    parser_logs.add_argument("service", nargs="*", help="Specific services to show logs from")

    parser_attach = subparsers.add_parser("attach", help="Attach to a service")
    parser_attach.add_argument("service", nargs=1, help="Specific service to attach to")

    parser_ps = subparsers.add_parser("ps", help="Show services state")

    parser_top = subparsers.add_parser("top", help="Show processes")

    parser_config = subparsers.add_parser("config", help="Show services config")

    options = parser.parse_args()

    if options.verbose:
        print(" ** Verbose **", file=sys.stderr)
        logging.basicConfig(level=logging.DEBUG)
        cfg_log.debug(f"Parsed options: {options}")
        cfg_log.debug(f"os.name = {os.name}")
    else:
        logging.basicConfig(level=logging.INFO)

    use_color = not options.no_color
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
    # TODO support multiple
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
            dotenv.load_dotenv(env_file, override=True)
        else:
            print("Provided environment file does not exist", file=sys.stderr)
            return 1

    compose = Compose(project_name, working_directory, service_files, verbose=options.verbose, use_color=use_color)

    if options.test_server is not None:
        compose.test_server(options.test_server, "GET")
    elif options.test_server_post is not None:
        compose.test_server(options.test_server_post, "POST")
    elif hasattr(options, "command") and options.command is not None:
        services = None
        if hasattr(options, "service") and len(options.service) > 0:
            services = options.service

        if options.command == "up":
            asyncio.run(compose.up(services))
        elif options.command == "start":
            asyncio.run(compose.start(services))
        elif options.command == "build":
            asyncio.run(compose.build(services))
        elif options.command == "down":
            asyncio.run(compose.down(services))
        elif options.command == "stop":
            asyncio.run(compose.stop(services))
        elif options.command == "logs":
            asyncio.run(compose.logs(services))
        elif options.command == "attach":
            asyncio.run(compose.attach(services[0]))
        elif options.command == "config":
            pprint.pprint(compose.service_config)
        elif options.command == "ps":
            asyncio.run(compose.ps())
        elif options.command == "top":
            asyncio.run(compose.top())
        else:
            print(f"Unhandled option {options.command}")
    else:
        parser.print_help()
