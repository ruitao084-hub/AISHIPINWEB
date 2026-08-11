"""Redis access (taskbook §4.5, P1-T06).

One wrapper shared by the API and the workers. Redis serves four distinct jobs
in this system and they must not each open their own ad-hoc client:

* Celery broker and result backend (ADR-0004)
* rate limiting (§123)
* provider circuit-breaker health state (§56)
* distributed job locks (§119)
"""

from backend_core.cache.client import (
    close_redis,
    get_redis,
    ping_redis,
    reset_redis_cache,
)

__all__ = ["close_redis", "get_redis", "ping_redis", "reset_redis_cache"]
