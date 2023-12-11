import signal


def get_stop_signal(config: dict) -> signal.Signals:
    s = config.get("stop_signal", signal.SIGTERM)
    if isinstance(s, int):
        s = signal.Signals(s)
    if isinstance(s, str):
        if hasattr(signal.Signals, s):
            s = getattr(signal.Signals, s)
        else:
            raise ValueError(f"Unknown signal {s}")
    return s


def get_stop_grace_period(config: dict) -> float:
    return config.get("stop_grace_period", 10)
