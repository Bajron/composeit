from composeit.service_config import get_stop_signal
import signal

import pytest

def test_get_stop_signal():
    assert get_stop_signal({}) == signal.SIGTERM
    assert get_stop_signal({"stop_signal": "SIGINT"}) == signal.SIGINT
    assert get_stop_signal({"stop_signal": 2}) == signal.SIGINT

    with pytest.raises(ValueError):
        assert get_stop_signal({"stop_signal": "SIGZZZ"})

    with pytest.raises(ValueError):
        assert get_stop_signal({"stop_signal": 12345})

