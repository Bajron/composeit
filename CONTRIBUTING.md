# Contributing

Contributions are welcome!
Open [an issue](https://github.com/Bajron/composeit/issues/new) or send a pull request.

This document describes setup for local development and testing.

## Style

Note the style and design is "keep it simple" and "make it work".
External dependencies and excessive abstractions should be avoided.

Format the code with `black`.

## Local installation

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

## Testing

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

## Manual test

```
cd test/unios
```

```
composeit -d up
composeit ps
composeit logs # ctrl+c to stop
composeit down
```

Note that you can also run `tests/integration/projects/*` manually.
