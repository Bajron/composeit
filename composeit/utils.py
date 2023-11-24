def duration_to_seconds(duration):
    if isinstance(duration, (int, float)):
        return duration
    # TODO
    raise Exception(f"Duration {duration} not handled. Only numbers supported for now")
