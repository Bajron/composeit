# Translation from docker-compose features

This file tracks features mimicked from the [docker-compose](https://docs.docker.com/engine/reference/commandline/docker/).

Commandline and schema lists are prefixed with a checkbox that serves as a TODO tracking.
This way features that potentially make sense are signalled and tracked.

## build

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

## ~~cp~~

Not implemented. Not applicable.

## create

`create` does not make sense as there is no image -> container separation.

See `build` which prepares the process to run.

## down

Stops services and triggers `clean` section for each service.

Commandline arguments:
* `[x]` `--timeout|-t` - shutdown timeout in seconds.

Service keys:
* `[x]` `clean`

Clean section works just like `build` section but it is executed after shutting down a service.

## _events_

Not implemented.

Perhaps could be adapted to stream services statuses with the ad hoc daemon.

## ~~exec~~

Not implemented. Not applicable.

Without dockers it would result in a possibility to exec anything in the machine exposing the ad hoc daemon.
Big no-no for security reasons. This tool is not about executing arbitrary commands, but a set of defined applications.

## images

Lists processes paths used to start a service.

Note that for services defined with `shell: true` you get simply a `<shell>` string.
It is a shell provided by the Python [`subprocess` module](https://docs.python.org/3/library/subprocess.html).

For regular process calls (`shell: false`) the binary is always resolved with `shutil.which`.
Note that this resulution happens in the main script context.
Environment variables specified for certain services does not matter for the lookup.

Note that the path resolution might depend on the `build`/`clean` command execution sequence or your virtual environment.

See the warning in https://docs.python.org/3/library/subprocess.html#subprocess.Popen
and https://docs.python.org/3/library/shutil.html#shutil.which

## kill

Sends a signal to services.

Commandline arguments:
* `[x]` `--signal|-s` Signal to send, by default SIGKILL

Note that this option is independent from the service option `stop_signal`.

Note that this is very low level feature and does not handle child processes.
Only the top level process of a service receives the signal.
If you want to stop the service use `stop` (second `stop` triggers forced kill).

Available signals depend on Python implementation.

## logs

Show logs from the services.
Only certain window of recent logs is stored for each process.
There are no advanced logging storage drivers.

Commandline arguments:
* `[x]` `--follow|-f`
* `[x]` `--with-context`
* `[x]` `--no-color`
* `[x]` `--no-log-prefix`
* `[x]` `--since`
* `[x]` `--tail|-n`
* `[x]` `--timestamps|-t`
* `[x]` `--until`

Service keys:
* `[x]` `logging`
    * `[x]` `none` - no logs are gathered for a service
    * `[ ]` `driver`
        * `logging.config.dictConfig`
           uses `config` from `driver` level as the `logging` module config
        * `logging.config.dictConfig.shared`
           uses `config` from `driver` level as the `logging` module config;
           configs are merged into a signle config and applied,
           so handlers etc. can be referenced across services

## ~~ls~~

Not applicable. There is no central database/daemon that could be asked for all composeit projects.
On Windows, the pipe paths could be listed perhaps, but on Linux whole filesystem would be scanned.

This command does not fit the ad hoc nature of the `composeit` daemon.

## _pause_

Not implemented.

Could be implemented in theory... but would be problematic.

## ~~port~~

Not applicable. `port` option is not handled in services YAML specification too.

## ps

Shows the status of services.
When server is not running, lists the services based on the config.

Commandline arguments:
* `[ ]` `--all|-a` - not sure if it is applicable
* `[ ]` `--filter` - maybe some day
* `[~]` `--format` - experimental, and not consistent
* `[x]` `--no-trunc` - Applied only to command
* `[ ]` `--orphans` - could make sense with dynamic config update
* `[~]` `--quiet` - kind of implemented
* `[ ]` `--services` - is it applicable?, not sure what it is
* `[~]` `--status` - based on process states

## ~~pull~~

Not implemented. Not applicable.
Downloading binaries from the web some day? Not likely.

## ~~push~~

Not implemented. Not applicable.
Downloading binaries to the web some day? Not likely.

## restart

Restarts processes.

Commandline arguments:
* `[x]` `--no-deps`
* `[x]` `--timeout|-t`

## ~~rm~~

Not implemented. See `down` for removing built applications.

## _run_

Not implemented.
Could add arguments to the process maybe.

## start

Starts services

## stop

Stops services

Commandline arguments:
* `[x]` `--timeout|-t`

## top

Display running processes.

## _unpause_

Not implemented. Maybe if there is pause...

## up

Commandline arguments:
* `[x]` `--abort-on-container-exit`
* `[ ]` `--always-recreate-deps`
* `[x]` `--attach`
* `[x]` `--attach-dependencies`
* `[x]` `--build`
* `[x]` `--build-arg`
* `[~]` `-d|--detach`
* `[ ]` `--force-recreate` Stop and build
* `[x]` `--exit-code-from`
* `[x]` `--no-attach`
* `[x]` `--no-build`
* `[x]` `--no-color`
* `[x]` `--no-deps`
* `[x]` `--no-log-prefix`
* `[x]` `--no-start`		Don't start the services after creating them.
* `[ ]` `--remove-orphans`
* `[ ]` `--scale` Maybe some day
* `[x]` `-t|--timeout`
* `[x]` `--timestamps`
* `[ ]` `--wait`
* `[ ]` `--wait-timeout`

## version

Commandline arguments:
* `[ ]` `-f|--format`
* `[ ]` `--short`

## wait

Not implemented yet.

Commandline arguments:
* `[ ]` `--down-project`

## _watch_

Not implemented. Could be feasible...
Would require more explicit dependencies than the current approach to build.
