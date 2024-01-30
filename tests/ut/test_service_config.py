from composeit.service_config import get_stop_signal, get_signal
import signal
import os

import pytest


def test_get_stop_signal():
    assert get_stop_signal({}) == signal.SIGTERM
    assert get_stop_signal({"stop_signal": "SIGINT"}) == signal.SIGINT
    assert get_stop_signal({"stop_signal": 2}) == signal.SIGINT

    with pytest.raises(ValueError):
        assert get_stop_signal({"stop_signal": "SIGZZZ"})

    with pytest.raises(ValueError):
        assert get_stop_signal({"stop_signal": 12345})


def test_get_signal():
    if os.name == "nt":
        assert get_signal("CTRL_C_EVENT") == signal.CTRL_C_EVENT
        assert get_signal(get_signal("CTRL_C_EVENT")) == signal.CTRL_C_EVENT

    assert get_signal(15) == signal.SIGTERM
    assert get_signal(get_signal(15)) == signal.SIGTERM
    assert get_signal(signal.SIGTERM) == signal.SIGTERM
    assert get_signal("SIGTERM") == signal.SIGTERM
