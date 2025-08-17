import os
import time

import redis
from fastapi import HTTPException, Request

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")

# Connect to Redis (adjust host/port as needed)
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)


# Configurable rate limiter dependency
def rate_limiter(limit: int = 5, window: int = 60):
    def dependency(request: Request):
        client_ip = request.client.host
        now = int(time.time())
        key = f"ratelimit:{client_ip}:{now // window}"

        # Atomically increment
        count = r.incr(key)
        if count == 1:
            # First request in this window — set expiry
            r.expire(key, window)

        if count > limit:
            raise HTTPException(status_code=429, detail="Too Many Requests")

    return dependency
