# Translation from docker-compose features

This file tracks features mimicked from the [docker-compose](https://docs.docker.com/engine/reference/commandline/docker/).

Commandline and schema lists are prefixed with a checkbox that serves as a TODO tracking.
This way features that potentially make sense are signalled and tracked.

## build
There is no docker image to build.
This option is reused to provide a way to prepare the process to run.

Commandline arguments:
* `[x]` `--build-arg` - arguments in format `<key>=<value>`, provided as environemnt variable to the build command.

Service keys:
* `[x]` `build`

Building process executes commands defined in the `build` key for a service.
Currently `build` key can have `shell_sequence` which is a list of shell commands to execute and general environment definition (`environment`, `env_file`, `inherit_env`).
If a shell command is provided as a list, it is regular process call.

Note that dependencies of the executable file itself cannot be handled.
The assumption is the executed shell commands delegate to a build system
that can handle dependencies and incremental builds.

If the service to build depends on a different service, the dependency is built first.

## config

Shows config after merges and other processing.

Commandline arguments:
* `[x]` `--format` `[yaml|json]`
* `[ ]` `--no-consistency`
* `[x]` `--no-interpolate`
* `[ ]` `--no-normalize`
* `[ ]` `--no-path-resolution`
* `[x]` `--output|-o`
* `[x]` `--quiet|-q`
* `[x]` `--services`

Toplevel config keys:
* `[x]` `services`

Note that merging configs is experimental and requires far more testing.

Note that some of the options might exist in the CLI, yet a lack of a tick on the list above
signals there is no actual implementation of the feature (using the option makes no difference).
This is indeed leaking the implementation details.
Now you know what DOES NOT take place and how experimental/in progress this software is.

## create

`create` does not make sense as there is no image -> container separation.

See `build` which prepares the process to run.

## down


## start

## up
