"""Per-identity token bucket, ported from CW Manage 3's `rate-limit.ts`.

Distinct from ClickUp's own ceiling. ClickUp limits per token (i.e. per user) and
returns 429 — `client.py` handles that. This bucket exists for the other failure
mode: a model in a loop issuing hundreds of calls, which would burn the user's
ClickUp budget and, with destructive tools enabled, could do real damage fast.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from clickup_mcp.client import ClickUpError
from clickup_mcp.constants import RATE_CAPACITY, RATE_REFILL_PER_MINUTE

logger = logging.getLogger(__name__)


class RateLimitExceeded(ClickUpError):
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail=(
                f"This MCP server's per-user rate limit was hit. Wait {retry_after}s "
                "before retrying. If you are looping, stop and reconsider the approach."
            ),
        )


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


_buckets: dict[int, _Bucket] = {}


def check(grant_id: int) -> None:
    """Consume one token for this user, or raise."""
    if RATE_CAPACITY <= 0:
        return

    now = time.monotonic()
    bucket = _buckets.get(grant_id)
    if bucket is None:
        _buckets[grant_id] = _Bucket(tokens=RATE_CAPACITY - 1, last_refill=now)
        return

    elapsed = now - bucket.last_refill
    bucket.tokens = min(RATE_CAPACITY, bucket.tokens + elapsed * (RATE_REFILL_PER_MINUTE / 60.0))
    bucket.last_refill = now

    if bucket.tokens < 1:
        deficit = 1 - bucket.tokens
        retry_after = max(1, int(deficit / (RATE_REFILL_PER_MINUTE / 60.0)) + 1)
        logger.warning(
            "Per-user rate limit exceeded",
            extra={"grant_id": grant_id, "retry_after": retry_after},
        )
        raise RateLimitExceeded(retry_after)

    bucket.tokens -= 1


def reset(grant_id: int | None = None) -> None:
    if grant_id is None:
        _buckets.clear()
    else:
        _buckets.pop(grant_id, None)
