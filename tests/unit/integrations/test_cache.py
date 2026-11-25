import pytest

from stockpilot.integrations.cache import TtlCache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


class Loader:
    """Returns 1, 2, 3...: the value tells how many times it was called."""

    calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.calls


def test_value_is_loaded_once_until_it_expires(clock: Clock) -> None:
    cache: TtlCache[str, int] = TtlCache(60, clock=clock)
    load = Loader()

    assert cache.get_or_load("k", load) == 1
    clock.now += 59.9
    assert cache.get_or_load("k", load) == 1
    clock.now += 0.1  # exactly 60 seconds: expired
    assert cache.get_or_load("k", load) == 2
    clock.now += 59.9  # the new value has its own 60 seconds
    assert cache.get_or_load("k", load) == 2


def test_each_key_has_its_own_value(clock: Clock) -> None:
    cache: TtlCache[str, int] = TtlCache(60, clock=clock)
    load = Loader()

    assert cache.get_or_load("a", load) == 1
    assert cache.get_or_load("b", load) == 2
    assert cache.get_or_load("a", load) == 1


def test_failures_are_not_cached(clock: Clock) -> None:
    cache: TtlCache[str, int] = TtlCache(60, clock=clock)

    def fail() -> int:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        cache.get_or_load("k", fail)

    assert cache.get_or_load("k", Loader()) == 1


def test_oldest_entry_leaves_when_it_is_full(clock: Clock) -> None:
    cache: TtlCache[str, int] = TtlCache(60, max_entries=2, clock=clock)
    load = Loader()
    cache.get_or_load("a", load)
    cache.get_or_load("b", load)
    cache.get_or_load("c", load)

    assert cache.get_or_load("b", load) == 2
    assert cache.get_or_load("c", load) == 3
    assert cache.get_or_load("a", load) == 4  # it had left


def test_renewed_entry_is_not_the_oldest(clock: Clock) -> None:
    cache: TtlCache[str, int] = TtlCache(60, max_entries=3, clock=clock)
    load = Loader()
    cache.get_or_load("a", load)
    clock.now += 30
    cache.get_or_load("b", load)
    clock.now += 30
    assert cache.get_or_load("a", load) == 3  # expired: loaded again

    cache.get_or_load("c", load)
    cache.get_or_load("d", load)

    assert cache.get_or_load("a", load) == 3
    assert cache.get_or_load("b", load) == 6  # the oldest one left
