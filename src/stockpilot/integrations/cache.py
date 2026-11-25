import time
from collections.abc import Callable
from threading import Lock


class TtlCache[K, V]:
    """Keeps each value for a limited time. Failed loads are never cached.

    Loading is not locked: two threads that miss at once both load, which costs
    one extra request, but a slow one never blocks the rest. Lives in memory, per process, so it is
    also limited in size: the oldest entry leaves when it is full.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Monotonic: a change of the system time neither expires nor extends entries.
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[K, tuple[float, V]] = {}

    def get_or_load(self, key: K, load: Callable[[], V]) -> V:
        entry = self._entries.get(key)
        if entry is not None and self._clock() < entry[0]:
            return entry[1]
        value = load()
        with self._lock:
            # Removed first, so a renewed entry goes to the end of the dict:
            # its order is the age order.
            self._entries.pop(key, None)
            while len(self._entries) >= self._max_entries:
                del self._entries[next(iter(self._entries))]
            self._entries[key] = (self._clock() + self._ttl, value)
        return value
