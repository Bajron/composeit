# composeit

## Notice
This package is still during prototyping / intensive development

## Installation

As usual with a Python package.

Prepare the virtual environemnt.
```
python -m venv venv
```

Activate the environment in `cmd`
```
.\venv\Scripts\activate.bat
```
or in `bash`
```
source ./venv/bin/activate
```

Install the package in editable mode. Use development variant to run tests etc.
```
pip install -e .[dev]
```

See what is possible for now
```
composeit --help
```

Run tests
```
pytest
```

Skip integration tests
```
pytest --ignore=tests/integration
```

## Manual test

In one window
```
composeit -f test/win/composeit.yml
```
or
```
composeit -f test/linux/composeit.yml
```


In the other one
```
composeit -f ./test/win/composeit.yml --test-server
```

## Why?

I wanted to organize a couple of processes on Windows, but I could not find a package that does that.

I wanted `docker-compose` without Docker.

There is `supervisor` that could do the trick, but it is not supported on Windows.
I also is quite a serious package...

There are similar things like `pypyr` or `prefect`, but they kind of do a different thing.
I wanted to organize a set of long running processes, not a pipeline.

Later I also found `honcho`, seems ok for the task.

There is a package called `just-compose` (wanted that name ;( ).
I foudn it late. It seems to be kind of what I want.
I could not find a documentation page for this one.
The example seems to be quite promising though.
The PyPy page suggests some specific use case, so I did not try it really.
It does seem similar to `docker-compose` yaml.
This could be what I wanted, but...

I figured I can write it myself, have some fun
and learn a little bit of asyncio by the way.

## Purpose

Have `docker-compose` without dockers.

Sure, just running processes is not the same as running dockers,
but the interface of docker-compose seems quite ok for organizing processes.

I strive for intuitive interface using familiarity with
the tool from the dockers' world.
Surely not all concepts will translate well to just processes
but we still get the advantage of readability and familiarity.

Some of the features also will not work because we do not have the docker daemon,
but this is ok. Let's see what we can get.

## TODO
docker-compose
- env_file
- handling .env file for compose environment
- entrypoint vs command/args
- up, down, build commands
- other commands
- depends_on behavior
- variable substitution (${:-})
- labels
- healthcheck
- logging
- stop_grace_period
- stop_signal
- ulimits, maybe on Linux?
- restart policy - delay, max attempts, window
- extensions fields? is it just working by default(yaml anchors)
- scaling? could be done.. but
- cap add/drop, could be done on Linux I guess, but would require root
- build as dependecy/prepare operation
  (new configuration fields, as dockerfile or context might not have sense)
- new config loading into running daemon

options:
- project-name
- project directory
- env-file
- no colors
- ansi (never, always, auto)
- log level
- verbose

commands that make sense:
- build
- config
- up/run/start
- down/kill/stop
- restart
- top/ps
- version
- logs
- scale

python:
 - variants of package - with different features if avialable.
   colors or command server is not required to run processes - optional dependencies

## Ideas of `docker-compose` -> `compose-it` feature translation

- `build` - Could be preparing the process.
    For example several commands to produce a binary.
    Perhaps should be just a single command? (we still have scripting)
    Having a `clean` would be nice as well
```
  foo:
    build:
      shell_sequence: # good to introduce to change the method later
        #- git clone ...?
        - mkdir "${DST}"
        - cmake -S "${SRC}" -B "${DST}" -D "CMAKE_BUILD_TYPE=${TYPE}"
        - cmake --build "${DST}" --config "${TYPE}" "${@}"
    clean:
      shell_sequence:
        - rm "${DST}" -rf

  bar:
    build:
      shell_sequence:
        - pip install xx  # this is interesting, which pip?
    clean:
      shell_sequence:
        - pip uninstall xx

  baz:
    build:
      shell_sequence:
        - cd xx && cargo build
```

- OS dependant behavior?

## Similar tools

It seems creating own compose-like tool is not that uncommon :)

* https://github.com/F1bonacc1/process-compose

