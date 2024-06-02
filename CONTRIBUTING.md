# Contributing

Contributions are welcome!
Open [an issue](https://github.com/Bajron/composeit/issues/new) or send a pull request.

Note that this project is still in early phase or will never stop being just a toy/experiment
(see [TODO.md](TODO.md) and [COMPOSE.md](COMPOSE.md)).
Share your ideas and remarks before preparing a big code contribution not to double/waste the effort.

This document describes setup for local development and testing.

## Style

Note the style and design is "keep it simple" and "make it work".
External dependencies and excessive abstractions should be avoided.

Format the code with `black`.

## Local installation

As usual with a Python package.

Prepare the virtual environment.
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

## Testing

Check formatting
```
black --check composeit tests
```
or just format it
```
black composeit tests
```

Run tests
```
pytest
```

Skip integration tests
```
pytest --ignore=tests/integration
```

Static analysis
```
mypy --check-untyped-defs composeit
```

## Thorough testing

Install tox
```
pip install tox
```

Run tox
```
tox
```

## Manual test

```
cd examples/unios
```

```
composeit -d up
composeit ps
composeit logs # ctrl+c to stop
composeit down
```

Note that you can also run `tests/integration/projects/*` manually.
