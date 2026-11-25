import time
from collections.abc import Callable


class TtlCache[K, V]:
    """Keeps each value for a limited time. Failed loads are never cached.

    Not locked: two threads that miss at once both load, which costs one extra
    request and is otherwise harmless. Lives in memory, per process.
    """

    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        # Monotonic: a change of the system time neither expires nor extends entries.
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[K, tuple[float, V]] = {}

    def get_or_load(self, key: K, load: Callable[[], V]) -> V:
        entry = self._entries.get(key)
        if entry is not None and self._clock() < entry[0]:
            return entry[1]
        value = load()
        self._entries[key] = (self._clock() + self._ttl, value)
        return value
