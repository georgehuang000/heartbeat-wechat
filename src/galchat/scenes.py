"""Cosmetic scene choices. They carry no conversation or relationship evidence."""

SCENE_IDS = ('study', 'cafe', 'park')


def default_scene_id(identity):
    """Stable visual variation from an account-scoped identity (not a security hash).

    The same UTF-8 FNV-1a calculation is used by the offline frontend. Reading a
    preference never rerolls or writes a file; explicit selections persist.
    """
    value = 2166136261
    for byte in str(identity).encode('utf-8'):
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return SCENE_IDS[value % len(SCENE_IDS)]
