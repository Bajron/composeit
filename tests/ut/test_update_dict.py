from composeit.utils import update_dict


def test_seperate_keys():
    a = {"a": "x"}
    b = {"b": "y"}
    assert update_dict(a, b) == {"a": "x", "b": "y"}


def test_same_keys():
    a = {"a": "x"}
    b = {"a": "y"}
    assert update_dict(a, b) == {"a": "y"}


def test_seperate_keys_recursive():
    a = {"r": {"a": "x"}}
    b = {"r": {"b": "y"}}
    assert update_dict(a, b) == {"r": {"a": "x", "b": "y"}}


def test_same_keys_recursive():
    a = {"r": {"a": "x"}}
    b = {"r": {"a": "y"}}
    assert update_dict(a, b) == {"r": {"a": "y"}}
