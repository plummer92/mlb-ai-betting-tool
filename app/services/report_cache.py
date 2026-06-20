from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Hashable


_cache: dict[Hashable, tuple[datetime, Any]] = {}
_lock = Lock()


def get_ttl_cached(
    key: Hashable,
    ttl_seconds: int,
    builder: Callable[[], Any],
) -> Any:
    now = datetime.now(timezone.utc)
    expires_at: datetime | None = None
    cached: Any = None
    with _lock:
        entry = _cache.get(key)
        if entry:
            expires_at, cached = entry
            if expires_at > now:
                return cached

    value = builder()
    with _lock:
        _cache[key] = (now + timedelta(seconds=ttl_seconds), value)
    return value


def clear_report_cache(prefix: str | None = None) -> int:
    with _lock:
        if prefix is None:
            count = len(_cache)
            _cache.clear()
            return count
        keys = [key for key in _cache if isinstance(key, tuple) and key and key[0] == prefix]
        for key in keys:
            _cache.pop(key, None)
        return len(keys)
