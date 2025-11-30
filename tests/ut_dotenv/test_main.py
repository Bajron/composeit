import io
import logging
import os
import sys
import subprocess
import textwrap
from unittest import mock

import pytest

from .utils import as_env

import composeit.dotenv as dotenv
from composeit.dotenv.main import _walk_to_root, _resolve_bindings
from composeit.dotenv.parser import parse_stream, Binding, Original


@pytest.mark.parametrize(
    "before,key,value,expected,after",
    [
        ("", "a", "", (True, "a", ""), "a=''\n"),
        ("", "a", "b", (True, "a", "b"), "a='b'\n"),
        ("", "a", "'b'", (True, "a", "'b'"), "a='\\'b\\''\n"),
        ("", "a", '"b"', (True, "a", '"b"'), "a='\"b\"'\n"),
        ("", "a", "b'c", (True, "a", "b'c"), "a='b\\'c'\n"),
        ("", "a", 'b"c', (True, "a", 'b"c'), "a='b\"c'\n"),
        ("a=b", "a", "c", (True, "a", "c"), "a='c'\n"),
        ("a=b\n", "a", "c", (True, "a", "c"), "a='c'\n"),
        ("a=b\n\n", "a", "c", (True, "a", "c"), "a='c'\n\n"),
        ("a=b\nc=d", "a", "e", (True, "a", "e"), "a='e'\nc=d"),
        ("a=b\nc=d\ne=f", "c", "g", (True, "c", "g"), "a=b\nc='g'\ne=f"),
        ("a=b\n", "c", "d", (True, "c", "d"), "a=b\nc='d'\n"),
        ("a=b", "c", "d", (True, "c", "d"), "a=b\nc='d'\n"),
    ],
)
def prepare_file_hierarchy(path):
    """
    Create a temporary folder structure like the following:

        test_find_dotenv0/
        └── child1
            ├── child2
            │   └── child3
            │       └── child4
            └── .env

    Then try to automatically `find_dotenv` starting in `child4`
    """

    leaf = path / "child1" / "child2" / "child3" / "child4"
    leaf.mkdir(parents=True, exist_ok=True)
    return leaf


def test_find_dotenv_no_file_raise(tmp_path):
    leaf = prepare_file_hierarchy(tmp_path)
    os.chdir(leaf)

    with pytest.raises(IOError):
        dotenv.find_dotenv(raise_error_if_not_found=True, usecwd=True)


def test_find_dotenv_no_file_no_raise(tmp_path):
    leaf = prepare_file_hierarchy(tmp_path)
    os.chdir(leaf)

    result = dotenv.find_dotenv(usecwd=True)

    assert result == ""


def test_find_dotenv_found(tmp_path):
    leaf = prepare_file_hierarchy(tmp_path)
    os.chdir(leaf)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_bytes(b"TEST=test\n")

    result = dotenv.find_dotenv(usecwd=True)

    assert result == str(dotenv_path)


@mock.patch.dict(os.environ, {}, clear=True)
def test_load_dotenv_existing_file(dotenv_path):
    dotenv_path.write_text("a=b")

    result = dotenv.load_dotenv(dotenv_path)

    assert result is True
    assert os.environ == as_env({"a": "b"})


def test_load_dotenv_no_file_verbose():
    logger = logging.getLogger("composeit.dotenv.main")

    with mock.patch.object(logger, "info") as mock_info:
        result = dotenv.load_dotenv(".does_not_exist", verbose=True)

    assert result is False
    mock_info.assert_called_once_with(
        "Could not find environment configuration file %s.", ".does_not_exist"
    )


@mock.patch.dict(os.environ, {"a": "c"}, clear=True)
def test_load_dotenv_existing_variable_no_override(dotenv_path):
    dotenv_path.write_text("a=b")

    result = dotenv.load_dotenv(dotenv_path, override=False)

    assert result is True
    assert os.environ == as_env({"a": "c"})


@mock.patch.dict(os.environ, {"a": "c"}, clear=True)
def test_load_dotenv_existing_variable_override(dotenv_path):
    dotenv_path.write_text("a=b")

    result = dotenv.load_dotenv(dotenv_path, override=True)

    assert result is True
    assert os.environ == as_env({"a": "b"})


@mock.patch.dict(os.environ, {"a": "c"}, clear=True)
def test_load_dotenv_redefine_var_used_in_file_no_override(dotenv_path):
    dotenv_path.write_text('a=b\nd="${a}"')

    result = dotenv.load_dotenv(dotenv_path)

    assert result is True
    if os.name == "nt":
        # Variable is not overwritten, but variable expansion
        # uses the lowercase variable that was just defined in the file.
        assert os.environ == as_env({"a": "c", "d": "b"})
    else:
        assert os.environ == as_env({"a": "c", "d": "c"})


@mock.patch.dict(os.environ, {"a": "c"}, clear=True)
def test_load_dotenv_redefine_var_used_in_file_with_override(dotenv_path):
    dotenv_path.write_text('a=b\nd="${a}"')

    result = dotenv.load_dotenv(dotenv_path, override=True)

    assert result is True
    assert os.environ == as_env({"a": "b", "d": "b"})


@mock.patch.dict(os.environ, {}, clear=True)
def test_load_dotenv_string_io_utf_8():
    stream = io.StringIO("a=à")

    result = dotenv.load_dotenv(stream=stream)

    assert result is True
    assert os.environ == as_env({"a": "à"})


@mock.patch.dict(os.environ, {}, clear=True)
def test_load_dotenv_file_stream(dotenv_path):
    dotenv_path.write_text("a=b")

    with dotenv_path.open() as f:
        result = dotenv.load_dotenv(stream=f)

    assert result is True
    assert os.environ == as_env({"a": "b"})


def test_load_dotenv_in_current_dir(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_bytes(b"a=b")
    code_path = tmp_path / "code.py"
    code_path.write_text(
        textwrap.dedent(
            """
        import os
        from composeit.dotenv import load_dotenv

        load_dotenv(verbose=True)
        print(os.environ['a'])
    """
        )
    )
    os.chdir(tmp_path)

    result = subprocess.run([sys.executable, code_path], check=False, stdout=subprocess.PIPE).stdout

    assert result.decode().strip() == "b"


def test_load_dotenv_values_in_current_dir(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_bytes(b"a=b")
    code_path = tmp_path / "code.py"
    code_path.write_text(
        textwrap.dedent(
            """
        import os
        from composeit.dotenv import dotenv_values

        v = dotenv_values(verbose=True)
        print(v['a'])
    """
        )
    )
    os.chdir(tmp_path)

    result = subprocess.run([sys.executable, code_path], check=False, stdout=subprocess.PIPE).stdout

    assert result.decode().strip() == "b"


def test_dotenv_values_file(dotenv_path):
    dotenv_path.write_text("a=b")

    result = dotenv.dotenv_values(dotenv_path)

    assert result == {"a": "b"}


@pytest.mark.parametrize(
    "env,variables,override,expected",
    [
        ({"B": "c"}, {"a": "$B"}.items(), True, {"a": "$B"}),
        ({"B": "c"}, {"a": "${B}"}.items(), False, {"a": "c"}),
        ({"B": "c"}, [("B", "d"), ("a", "${B}")], False, {"a": "c", "B": "d"}),
        ({"B": "c"}, [("B", "d"), ("a", "${B}")], True, {"a": "d", "B": "d"}),
        ({"B": "c"}, [("B", "${X:-d}"), ("a", "${B}")], True, {"a": "d", "B": "d"}),
        ({"B": "c"}, {"a": "x${B}y"}.items(), True, {"a": "xcy"}),
        # Unfortunate sequence
        (
            {"B": "c"},
            [("C", "${B}"), ("B", "${A}"), ("A", "1")],
            True,
            {"C": "c", "B": "", "A": "1"},
        ),
        (
            {"B": "c"},
            [("C", "${B}"), ("B", "${A}"), ("A", "1")],
            False,
            {"C": "c", "B": "", "A": "1"},
        ),
        ({"B": "c"}, [("B", "x"), ("B", "${B}"), ("B", "${B}")], True, {"B": "x"}),
        ({"B": "c"}, [("B", "x"), ("B", "${B}"), ("B", "${B}")], False, {"B": "c"}),
        ({"B": "c"}, [("B", "x"), ("B", "${B}"), ("B", "y")], False, {"B": "y"}),
    ],
)
def test_resolve_variables(env, variables, override, expected):
    with mock.patch.dict(os.environ, env, clear=True):
        result = dotenv.main.resolve_variables(variables, override=override)
        assert result == expected


@pytest.mark.parametrize(
    "env,variables,value,override,expected",
    [
        ({"B": "c"}, {"B": "d"}, "$B", True, "$B"),
        ({"B": "c"}, {"B": "d"}, "${B}", True, "d"),
        ({"B": "c"}, {"B": "d"}, "${B}", False, "c"),
        ({}, {"B": "d"}, "${B}", False, "d"),
        ({"B": "c"}, {}, "${B}", True, "c"),
        ({"B": "c"}, {"A": "d"}, "${B}${A}", True, "cd"),
        ({"B": "c"}, {"B": "d"}, "$B$B$B", True, "$B$B$B"),
        ({"B": "c"}, {"B": "d"}, "${B}${B}${B}", True, "ddd"),
        ({"B": "c"}, {"B": "d"}, "${B}${B}${B}", False, "ccc"),
        ({"B": "c"}, {"B": "d"}, "${C}", False, ""),
        ({"B": "c"}, {"B": "d"}, "${C}", True, ""),
        ({"B": "c"}, {"B": "d"}, "${C}${C}${C}", True, ""),
        ({"B": "c"}, {"B": "d"}, "${C}a${C}b${C}", True, "ab"),
    ],
)
def test_resolve_variable(env, variables, value, override, expected):
    with mock.patch.dict(os.environ, env, clear=True):
        result = dotenv.main.resolve_variable(value, variables, override=override)
        assert result == expected


@pytest.mark.parametrize(
    "env,string,interpolate,expected",
    [
        # Use uppercase when setting up the env to be compatible with Windows
        # Defined in environment, with and without interpolation
        ({"B": "c"}, "a=$B", False, {"a": "$B"}),
        ({"B": "c"}, "a=$B", True, {"a": "$B"}),
        ({"B": "c"}, "a=${B}", False, {"a": "${B}"}),
        ({"B": "c"}, "a=${B}", True, {"a": "c"}),
        ({"B": "c"}, "a=${B:-d}", False, {"a": "${B:-d}"}),
        ({"B": "c"}, "a=${B:-d}", True, {"a": "c"}),
        # Defined in file
        ({}, "b=c\na=${b}", True, {"a": "c", "b": "c"}),
        # Undefined
        ({}, "a=${b}", True, {"a": ""}),
        ({}, "a=${b:-d}", True, {"a": "d"}),
        # With quotes
        ({"B": "c"}, 'a="${B}"', True, {"a": "c"}),
        ({"B": "c"}, "a='${B}'", True, {"a": "c"}),
        # With surrounding text
        ({"B": "c"}, "a=x${B}y", True, {"a": "xcy"}),
        # Self-referential
        ({"A": "b"}, "A=${A}", True, {"A": "b"}),
        ({}, "a=${a}", True, {"a": ""}),
        ({"A": "b"}, "A=${A:-c}", True, {"A": "b"}),
        ({}, "a=${a:-c}", True, {"a": "c"}),
        # Reused
        ({"B": "c"}, "a=${B}${B}", True, {"a": "cc"}),
        # Re-defined and used in file
        ({"B": "c"}, "B=d\na=${B}", True, {"a": "d", "B": "d"}),
        ({}, "a=b\na=c\nd=${a}", True, {"a": "c", "d": "c"}),
        ({}, "a=b\nc=${a}\nd=e\nc=${d}", True, {"a": "b", "c": "e", "d": "e"}),
        # No value
        ({}, "a\nb=${a}", True, {"a": None, "b": ""}),
        ({}, "a\nb=${a}", False, {"a": None, "b": "${a}"}),
    ],
)
def test_dotenv_values_string_io(env, string, interpolate, expected):
    with mock.patch.dict(os.environ, env, clear=True):
        stream = io.StringIO(string)
        stream.seek(0)

        result = dotenv.dotenv_values(stream=stream, interpolate=interpolate)

        assert result == expected


@pytest.mark.parametrize(
    "string,expected_xx",
    [
        ("XX=${NOT_DEFINED-ok}", "ok"),
        ("XX=${NOT_DEFINED:-ok}", "ok"),
        ("XX=${EMPTY-ok}", ""),
        ("XX=${EMPTY:-ok}", "ok"),
        ("XX=${TEST-ok}", "tt"),
        ("XX=${TEST:-ok}", "tt"),
        ("XX=${NOT_DEFINED+ok}", ""),
        ("XX=${NOT_DEFINED:+ok}", ""),
        ("XX=${EMPTY+ok}", "ok"),
        ("XX=${EMPTY:+ok}", ""),
        ("XX=${TEST+ok}", "ok"),
        ("XX=${TEST:+ok}", "ok"),
        ("XX=${EMPTY?no throw}", ""),
        ("XX=${TEST?no throw}", "tt"),
        ("XX=${TEST:?no throw}", "tt"),
    ],
)
def test_variable_expansions(string, expected_xx):
    test_env = {
        "TEST": "tt",
        "EMPTY": "",
    }
    with mock.patch.dict(os.environ, test_env, clear=True):
        stream = io.StringIO(string)
        stream.seek(0)

        result = dotenv.dotenv_values(stream=stream, interpolate=True)

        assert result["XX"] == expected_xx


@pytest.mark.parametrize(
    "string,message",
    [
        ("XX=${EMPTY:?throw}", "EMPTY: throw"),
        ("XX=${NOT_DEFINED:?throw}", "NOT_DEFINED: throw"),
        ("XX=${NOT_DEFINED?throw}", "NOT_DEFINED: throw"),
    ],
)
def test_required_variable_throws(string, message):
    test_env = {
        "TEST": "tt",
        "EMPTY": "",
    }
    with mock.patch.dict(os.environ, test_env, clear=True):
        stream = io.StringIO(string)
        stream.seek(0)

        with pytest.raises(LookupError, match=message):
            dotenv.dotenv_values(stream=stream, interpolate=True)


@pytest.mark.parametrize(
    "string,expected_xx",
    [
        ("XX=TEST", "TEST"),
        ('XX="TE"ST', "TEST"),
        ("XX='TE'ST", "TEST"),
        ("XX=\"TE\"'ST'", "TEST"),
        ("XX=TE'ST'", "TEST"),
        ('XX=TE"ST"', "TEST"),
        ("XX=TE ST", "TE ST"),
        ('XX=TE "ST"', "TE ST"),
        ("XX=$TEST", "$TEST"),
        ("XX=${TEST}", "tt"),
        ('XX="${TEST}"', "tt"),
        ("XX='${TEST}'", "tt"),
        ("XX='$TEST'", "$TEST"),
        ("XX='\\$\\{TEST\\}'", "\\$\\{TEST\\}"),
        ("XX=\\$\\{TEST\\}", "\\$\\{TEST\\}"),
        ('XX="\\$\\{TEST\\}"', "\\$\\{TEST\\}"),
    ],
)
def test_document_expansions(string, expected_xx):
    test_env = {"TEST": "tt"}
    with mock.patch.dict(os.environ, test_env, clear=True):
        stream = io.StringIO(string)
        stream.seek(0)

        result = dotenv.dotenv_values(stream=stream, interpolate=True)

        assert result["XX"] == expected_xx


@pytest.mark.parametrize(
    "string,expected_xx",
    [
        ("XX=${TEST}", "tt"),
        ('XX="${TEST}"', "tt"),
        ("XX='${TEST}'", "${TEST}"),
    ],
)
def test_single_quote_expansions(string, expected_xx):
    test_env = {"TEST": "tt"}
    with mock.patch.dict(os.environ, test_env, clear=True):
        stream = io.StringIO(string)
        stream.seek(0)

        result = dotenv.dotenv_values(stream=stream, interpolate=True, single_quotes_expand=False)

        assert result["XX"] == expected_xx


def test_not_closed_strings():
    stream = io.StringIO('AA="aa' + "\n" + "BB=bb" + "\n" + "CC='cc" + "\n" + "DD=dd")

    stream.seek(0)

    message = "Could not parse environment configuration statement starting at line %s"
    logger = logging.getLogger("composeit.dotenv.main")

    with mock.patch.object(logger, "warning") as mock_warning:
        result = dotenv.dotenv_values(stream=stream, verbose=False)
        assert result == {"BB": "bb", "DD": "dd"}
        # This warning is not controlled by the verbose
        # mock_warning.assert_not_called()
        mock_warning.assert_has_calls([mock.call(message, 1), mock.call(message, 3)])

    stream.seek(0)
    with mock.patch.object(logger, "warning") as mock_warning:
        result = dotenv.dotenv_values(stream=stream, verbose=True)
        assert result == {"BB": "bb", "DD": "dd"}
        mock_warning.assert_has_calls([mock.call(message, 1), mock.call(message, 3)])


def test_not_filtered_bindings():
    stream = io.StringIO('AA="aa' + "\n" + "BB=bb" + "\n" + "CC='cc" + "\n" + "DD=dd")
    # Theoretical scenario, _resolve_bindings received filtered list already
    result = _resolve_bindings(parse_stream(stream), False, False)
    assert result == {"BB": "bb", "DD": "dd"}

    # Explicit synthetic test
    result = _resolve_bindings([Binding(None, None, Original("", 0), False)], False, False)
    assert result == {}


def test_dotenv_values_file_stream(dotenv_path):
    dotenv_path.write_text("a=b")

    with dotenv_path.open() as f:
        result = dotenv.dotenv_values(stream=f)

    assert result == {"a": "b"}


def test_resolve_none_variable():
    resolved = dotenv.resolve_variables([("A", None)], override=True)
    assert "A" in resolved
    assert resolved["A"] is None


def test_edge_case_path_resolutions(tmp_path):
    # These cases are not really reachable in the current code.
    # The checks inside make sense, so they are covered here.

    not_existing = tmp_path / "not-existing"
    assert not not_existing.exists()
    with pytest.raises(OSError):
        # We return generator here, so we need to trigger actual execution
        list(_walk_to_root(str(not_existing)))

    f = tmp_path / ".env"
    f.touch()

    s = tmp_path / "x"
    s.touch()

    result = list(_walk_to_root(str(s)))
    assert result[0] == str(tmp_path)


def test_interactive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("A=xyz")
    commands = tmp_path / "commands"
    commands.write_text(
        textwrap.dedent(
            """
        from composeit.dotenv import load_dotenv
        import os
        load_dotenv() and None
        print(os.environ['A'])
    """
        )
    )
    result = subprocess.run(
        [sys.executable, "-i"], check=False, stdin=open(commands), stdout=subprocess.PIPE
    ).stdout
    assert result.decode().strip() == "xyz"


def test_script_from_stream(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("A=xyz")
    commands = tmp_path / "commands"
    commands.write_text(
        textwrap.dedent(
            """
        from composeit.dotenv import load_dotenv
        import os
        load_dotenv() and None
        print(os.environ['A'])
    """
        )
    )
    result = subprocess.run(
        [sys.executable], check=False, stdin=open(commands), stdout=subprocess.PIPE
    ).stdout
    assert result.decode().strip() == "xyz"
