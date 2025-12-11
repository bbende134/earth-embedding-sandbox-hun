import os
import time

import redis
from fastapi import HTTPException, Request

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
DISABLE_RATELIMIT = os.getenv("DISABLE_RATELIMIT", "false").lower() == "true"

# Connect to Redis (adjust host/port as needed)
if not DISABLE_RATELIMIT:
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    except Exception:
        r = None
else:
    r = None


def check_redis_connection():
    """Check if Redis is reachable."""
    if DISABLE_RATELIMIT:
        return True
    try:
        if r:
            r.ping()
            return True
        return False
    except redis.ConnectionError:
        return False


# Configurable rate limiter dependency
def rate_limiter(limit: int = 5, window: int = 60):
    def dependency(request: Request):
        if DISABLE_RATELIMIT:
            return

        client_ip = request.client.host
        now = int(time.time())
        key = f"ratelimit:{client_ip}:{now // window}"

        # Atomically increment
        if r:
            count = r.incr(key)
            if count == 1:
                # First request in this window — set expiry
                r.expire(key, window)

            if count > limit:
                raise HTTPException(status_code=429, detail="Too Many Requests")

    return dependency
