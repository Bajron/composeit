import argparse
import pathlib
import os
import sys
import logging
import dotenv
from .compose import Compose


def main():
    parser = argparse.ArgumentParser(
        prog="composeit",
        description="Define and run applications",
    )
    cfg_log = logging.getLogger("config")
    parser.add_argument(
        "-f", "--file", nargs="*", default=[], action="extend", type=pathlib.Path, help="Compose configuration file"
    )
    parser.add_argument("-p", "--project-name", default=None, type=str, help="Project name")
    parser.add_argument("--project-directory", default=None, type=pathlib.Path, help="Alternate working directory")
    parser.add_argument("--env-file", default=[], action="extend", type=pathlib.Path)
    parser.add_argument("--verbose", default=False, action="store_true")
    parser.add_argument("--no-color", default=False, action="store_true")

    parser.add_argument(
        "--test-server", default=None, help="Temporary option to perform GET with a provided URL on the server"
    )

    subparsers = parser.add_subparsers(help="sub-command help")
    parser_up = subparsers.add_parser("up", help="Startup the services")
    parser_up.add_argument("--build", default=False, action="store_true", help="Rebuild services")
    parser_up.add_argument("service", nargs="*", help="Specific service to start")

    parser_down = subparsers.add_parser("down", help="Close and cleanup the services")
    parser_down.add_argument("service", nargs="*", help="Specific service to close")

    options = parser.parse_args()
    print(options)

    if options.verbose:
        print(" ** Verbose **")
        logging.basicConfig(level=logging.DEBUG)

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

    compose = Compose(project_name, working_directory, service_files, not options.no_color)

    if options.test_server is not None:
        compose.side_action(options.test_server)
    else:
        compose.run()
