# Translation from docker-compose features

## build
There is no docker image to build.
This option is reused to provide a way to prepare the process to run.

Commandline arguments:
* `--build-arg` - arguments in format `<key>=<value>`, provided as environemnt variable to the build command.

Service keys:
* `build`

Building process executes commands defined in the `build` key for a service.
Currently `build` key can have `shell_sequence` which is a list of shell commands to execute and general environment definition (`environment`, `env_file`, `inherit_env`).
If a shell command is provided as a list, it is regular process call.

Note that dependencies of the executable file itself cannot be handled.
The assumption is the executed shell commands delegate to a build system
that can handle dependencies and incremental builds.

If the service to build depends on a different service, the dependency is built first.

## start

## up