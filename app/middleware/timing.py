from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

_SLOW_ENDPOINT_EVENTS = deque(maxlen=80)


def get_slow_endpoint_events(limit: int = 20) -> list[dict]:
    return list(_SLOW_ENDPOINT_EVENTS)[-limit:][::-1]


class EndpointTimingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, warn_ms: int = 2000):
        super().__init__(app)
        self.warn_ms = warn_ms

    async def dispatch(self, request: Request, call_next):
        started = perf_counter()
        response = await call_next(request)
        elapsed_ms = int((perf_counter() - started) * 1000)
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        if elapsed_ms >= self.warn_ms:
            event = {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
            }
            _SLOW_ENDPOINT_EVENTS.append(event)
            logger.warning(
                "[perf] slow endpoint method=%s path=%s status=%s elapsed_ms=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        return response
