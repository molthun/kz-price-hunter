"""Ограниченный кэш в памяти: потолок числа записей (LRU) и срок жизни (TTL).

Заменяет обычные словари, которые росли с каждым уникальным ключом до перезапуска
процесса (аудит M03). Интерфейс словаря: in, [], []=, get, pop, clear, len.
Потокобезопасен: кэши используются и из event loop, и из asyncio.to_thread.
"""
import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping

_SWEEP_EVERY = 256


class BoundedTTLCache(MutableMapping):
    def __init__(self, maxsize: int, ttl: float, clock=time.monotonic):
        if maxsize <= 0 or ttl <= 0:
            raise ValueError("maxsize и ttl должны быть положительными")
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._data: "OrderedDict[object, tuple]" = OrderedDict()
        self._lock = threading.Lock()
        self._writes = 0

    def _expired(self, stored_at: float, now: float) -> bool:
        return now - stored_at > self.ttl

    def __getitem__(self, key):
        with self._lock:
            stored_at, value = self._data[key]
            if self._expired(stored_at, self._clock()):
                del self._data[key]
                raise KeyError(key)
            self._data.move_to_end(key)
            return value

    def __setitem__(self, key, value):
        with self._lock:
            now = self._clock()
            self._data[key] = (now, value)
            self._data.move_to_end(key)
            self._writes += 1
            if self._writes % _SWEEP_EVERY == 0:
                self._sweep(now)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __delitem__(self, key):
        with self._lock:
            del self._data[key]

    def __contains__(self, key):
        try:
            self[key]
            return True
        except KeyError:
            return False

    def __iter__(self):
        with self._lock:
            now = self._clock()
            return iter([k for k, (t, _) in self._data.items() if not self._expired(t, now)])

    def __len__(self):
        with self._lock:
            self._sweep(self._clock())
            return len(self._data)

    def clear(self):
        with self._lock:
            self._data.clear()

    def _sweep(self, now: float) -> None:
        for key in [k for k, (t, _) in self._data.items() if self._expired(t, now)]:
            del self._data[key]
