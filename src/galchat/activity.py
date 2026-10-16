"""Cooperative checkpoints for foreground-only work; no GUI or network calls."""


class ActivityPaused(RuntimeError):
    code = 'background_paused'


def check_activity(callback):
    if callable(callback):
        callback()
