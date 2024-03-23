# composeit

## Notice

This package is still during prototyping / intensive development.

Currently it is as a toy project, not a production ready solution.

It might be just good enough for your use case.
See the similar tools section below if you are looking for a mature solution.

## Purpose

Have `docker-compose` without dockers.

Sure, just running processes is not the same as running dockers,
but the interface of `docker-compose` seems quite ok for organizing processes.

The target is to have an intuitive interface based on the tool from the dockers' world.
Surely not all concepts will translate well to just processes
but we still get the advantage of readability and familiarity.

Some of the features also will not work because we do not have the docker daemon.
This is ok. Let's see what we can get.

## Quickstart

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

## Why?

I wanted to organize a couple of processes on Windows, but I could not find a package that does that.

I wanted `docker-compose` without Docker.

There is `supervisor` that could do the trick, but it is not supported on Windows.
I also is quite a serious package...

There are similar things like `pypyr` or `prefect`, but they kind of do a different thing.
I wanted to organize a set of long running processes, not a pipeline.

Later I also found `honcho`, seems ok for the task.

There is a package called `just-compose` (wanted that name ;( ).
I found it late. It seems to be kind of what I want.
I could not find a documentation page for this one.
The example seems to be quite promising though.
The PyPy page suggests some specific use case, so I did not try it really.
It does seem similar to `docker-compose` yaml.
This could be what I wanted, but...

I figured I can write it myself, have some fun and learn a little bit of asyncio by the way.


## TODO
Checkout `poetry` and `pipx`

## Thanks

`python-dotenv` was a great example of setting up a Python project for me.
Loading environemnt and variable expansion uses its implementation.

I did not have patience to wait for my proposals there.
This package uses my fork of the package.
https://github.com/Bajron/python-dotenv/tree/v1.1.0

For this reason separate virtual environment or `pipx` is recommended to avoid a versioning clash.

## Similar tools

It seems creating own compose-like tool is not that uncommon :)

Checkout these projects too:
* https://github.com/F1bonacc1/process-compose

You might be interested in these as well:
* https://docs.docker.com/compose/
