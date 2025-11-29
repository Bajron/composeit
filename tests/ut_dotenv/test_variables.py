import pytest

from composeit.dotenv.variables import Literal, Variable, Action, parse_variables


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", []),
        ("a", [Literal(value="a")]),
        ("${a}", [Variable(name="a", action=None)]),
        ("${a:-b}", [Variable(name="a", action=Action(":-", "b"))]),
        ("${a-b}", [Variable(name="a", action=Action("-", "b"))]),
        ("${a:+b}", [Variable(name="a", action=Action(":+", "b"))]),
        ("${a+b}", [Variable(name="a", action=Action("+", "b"))]),
        ("${a:?b}", [Variable(name="a", action=Action(":?", "b"))]),
        ("${a?b}", [Variable(name="a", action=Action("?", "b"))]),
        ("${a??b}", [Variable(name="a", action=Action("?", "?b"))]),
        # Unsupported
        ("${a:b}", [Literal(value="${a:b}")]),
        ("${a!b}", [Variable(name="a!b", action=None)]),
        (
            "${a}${b}",
            [
                Variable(name="a", action=None),
                Variable(name="b", action=None),
            ],
        ),
        (
            "a${b}c${d}e",
            [
                Literal(value="a"),
                Variable(name="b", action=None),
                Literal(value="c"),
                Variable(name="d", action=None),
                Literal(value="e"),
            ],
        ),
    ],
)
def test_parse_variables(value, expected):
    result = parse_variables(value)

    assert list(result) == expected


def test_types_support():
    result = list(parse_variables("a${b}${c:-d}"))

    # Basic comparison
    assert result[0] == Literal(value="a")
    assert result[0] != Literal(value="b")

    # Repr
    assert repr(result[0]) == "Literal(value='a')"
    assert repr(result[1]) == "Variable(name='b', action=None)"
    assert repr(result[2]) == "Variable(name='c', action=Action(spec=':-', argument='d'))"

    # Comparison with other classes
    for a in result:
        assert a.__eq__("x") == NotImplemented
        assert a.__ne__("x") == NotImplemented

    assert isinstance(result[2], Variable)
    assert result[2].action.__eq__("x") == NotImplemented
