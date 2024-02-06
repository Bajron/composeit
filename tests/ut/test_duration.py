from composeit.utils import duration_to_seconds


def test_value():
    assert duration_to_seconds(1) == 1
    assert duration_to_seconds(1.5) == 1.5


def test_single_specifier():
    assert duration_to_seconds("1h") == 60 * 60
    assert duration_to_seconds("1hours") == 60 * 60
    assert duration_to_seconds("1m") == 60
    assert duration_to_seconds("1minute") == 60
    assert duration_to_seconds("1s") == 1
    assert duration_to_seconds("1second") == 1
    assert duration_to_seconds("1") == 1
    assert duration_to_seconds("1.5") == 1.5


def test_white_space():
    assert duration_to_seconds(" 1m") == 60
    assert duration_to_seconds(" 1minute ") == 60
    assert duration_to_seconds("1minute ") == 60


def test_case():
    assert duration_to_seconds(" 1M") == 60
    assert duration_to_seconds(" 1mINute ") == 60
    assert duration_to_seconds("1Minute ") == 60


def test_two_specifier():
    assert duration_to_seconds(" 1m 1s") == 61
    assert duration_to_seconds(" 1minute 1second") == 61
    assert duration_to_seconds("1minutes 1seconds") == 61


def test_negative():
    assert duration_to_seconds("- 1.5m 1s") == -91
    assert duration_to_seconds(" -1.5minute 1second") == -91
    assert duration_to_seconds("-1.5minutes 1seconds") == -91


def test_bad_input():
    assert duration_to_seconds("----1s") == -1
    assert duration_to_seconds(" -1.5mi nute 1second") == -91
    assert duration_to_seconds("second") == 0
