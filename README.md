# composeit

Process composing utility

## Notice

This package is still during prototyping / intensive development.

Currently it is as a toy project, not a production ready solution.
Checkout a rough plan of development in [TODO.md](TODO.md) and [COMPOSE.md](COMPOSE.md)

It might be just good enough for your use case, but no guarantees.
See the similar tools section at the end if you are looking for a mature solution.

## Purpose

Have `docker-compose` without dockers.

Sure, just running processes is not the same as running dockers,
but the interface of `docker-compose` seems quite ok for organizing processes.

The target is to have an intuitive interface based on the tool from the dockers' world.
Surely not all concepts will translate well to just processes
but we still get the advantage of readability and familiarity.

Some of the features also will not work because we do not have the docker daemon.
This is ok. Let's see what we can get.

## Installing

Direct installation via git is the recommended way of installing the package.
You can use:
* git+https://github.com/Bajron/composeit.git
* git+ssh://git@github.com/Bajron/composeit

### pipx

[`pipx`](https://pipx.pypa.io/) is a tool for installing
Python command line tools in dedicated environments.
Seems like a perfect match for `composeit`

Get `pipx` and then:
```
pipx install https://github.com/Bajron/composeit
```

(until the package is public you can use `git+ssh://git@github.com/Bajron/composeit`)

### pip

Separate environment is recommended because of the custom `python-dotenv`
```
pip install --upgrade https://github.com/Bajron/composeit
```

### Official package isnot fully functional

There is only a limited support for variable expansions.

A package with a direct dependency (custom `python-dotenv` in our case) cannot be uploaded to PyPI.

Some day:
```
pip install --upgrade composeit
```
...or similar with `pipx` should work as well.

To make it fully functional you can add the custom `python-dotenv` with
```
pip install --upgrade python-dotenv@git+https://github.com/Bajron/python-dotenv.git@v1.1.0
```

### Quick start hacking this repository

```
git clone https://github.com/Bajron/composeit
cd composeit
python -m venv venv
```

```
source ./venv/bin/activate
```
or
```
.\venv\Scripts\activate.bat
```

```
pip install -e .
cd examples/unios
composeit -up # ctrl+c to stop
```

Also see [CONTRIBUTING.md](CONTRIBUTING.md)

## Documentation

See [COMPOSE.md](COMPOSE.md) for features comparison to `docker-compose`.
The document is created for tracking and explaining ideas.

Run the tool itself for more info about certain options.
```
composeit --help
```
or
```
composeit <command> --help
```

## Ad hoc process server

To achieve certain features with the docker daemon missing,
an ad hoc HTTP server is created that listens on a named pipe.

The pipe is created in the project directory on Linux.
On Windows the pipe is created in `.\\pipe\\`.
Default access rights are applied.

## Known issues

### Windows

* Process handling on Windows is very different from Linux (e.g. signal support).
There is no real graceful shutdown for processes at the moment.

* Note that on Windows environment variable names in Python are
[always uppercase](https://docs.python.org/3/library/os.html#os.environ)!
This may lead to some unexpected expansions if your variables are not all uppercase.

## python-dotenv

[`python-dotenv`](https://github.com/theskumar/python-dotenv)
was a great example of setting up a Python project for me.

Loading environment and variable expansion uses its implementation.

I did not have patience to wait for my proposals there.
This package uses my fork of the package.
https://github.com/Bajron/python-dotenv/tree/v1.1.0

For this reason separate virtual environment or `pipx` is recommended to avoid a version clash.

## Why another tool?

I wanted to organize a couple of processes on Windows, but I could not find a package that does that.

I wanted [`docker-compose`](https://docs.docker.com/compose/) without Docker.

There is [`supervisor`](https://pypi.org/project/supervisor/) that could do the trick,
but it is not supported on Windows. It also is quite a serious package...

There are similar things like [`pypyr`](https://pypi.org/project/pypyr/)
or [`prefect`](https://pypi.org/project/prefect/), but they do a different thing.
I wanted to organize a set of long running processes, not a pipeline.

Later I also found [`honcho`](https://pypi.org/project/honcho/), seems ok for the task.

There is also a package called [`just-compose`](https://pypi.org/project/just-compose/) (wanted that name! ;( ).
I found it late. It seems to be kind of what I want.
The example seems to be quite promising.
The PyPy page suggests some specific use case, and I did not try it really.
It does seem similar to [`docker-compose` yaml](https://docs.docker.com/compose/compose-file/).
This could be what I wanted, but...

I figured I can write it myself, have some fun
and learn a little bit of Python and `asyncio` by the way.

## Similar tools

It seems creating own compose-like tool is not that uncommon :)

Checkout these projects too for process organization:
* https://github.com/F1bonacc1/process-compose
* https://pypi.org/project/just-compose/
* https://pypi.org/project/honcho/
* https://pypi.org/project/supervisor/
* https://docs.docker.com/compose/

You might be interested in these as well:
* https://pypi.org/project/pypyr/ for pipeline orchestration
* https://pypi.org/project/prefect/ for pipeline orchestration
* https://github.com/theskumar/python-dotenv for `.env` files handling
