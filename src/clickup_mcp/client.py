"""HTTP client for the ClickUp API, scoped per authenticated user.

Two things here differ from the single-user servers in this estate and both are
load-bearing:

* **The cache is namespaced by grant.** Whoop keys its cache `(path, params)`.
  Reused verbatim in a multi-user server that is a data leak — user A's task list
  served to user B on a cache hit. Every entry here lives under a grant id.
* **There is no token refresh.** ClickUp access tokens never expire and no refresh
  token is issued, so a 401 means the user revoked the integration. Retrying is
  pointless; the only recovery is re-authorization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from clickup_mcp.constants import (
    CLICKUP_API_BASE,
    HTTP_TIMEOUT,
    MAX_CACHE_ENTRIES_PER_GRANT,
    RETRYABLE_STATUS,
)
from clickup_mcp.store import ClickUpGrant

logger = logging.getLogger(__name__)


class ClickUpError(Exception):
    """A ClickUp API call failed."""

    def __init__(self, status_code: int, detail: str, ecode: str | None = None):
        self.status_code = status_code
        self.detail = detail
        self.ecode = ecode
        super().__init__(f"ClickUp API error {status_code}: {detail}")


class ClickUpAuthError(ClickUpError):
    """The caller is not authorized — re-authorization is the only fix."""

    def __init__(self, detail: str = "ClickUp authorization is missing or revoked."):
        super().__init__(status_code=401, detail=detail)


class ClickUpRateLimitError(ClickUpError):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            status_code=429,
            detail=(
                f"ClickUp rate limit exceeded. Retry in {retry_after_seconds}s. "
                "The limit is per user token: 100/min on Free, Unlimited and Business "
                "plans, 1,000 on Business Plus, 10,000 on Enterprise."
            ),
        )


def _cache_ttl_for(method: str, path: str) -> int:
    """Seconds to cache a response. 0 means do not cache."""
    if method != "GET":
        return 0
    # Profile and workspace list barely move.
    if path in ("/v2/user", "/v2/team"):
        return 900
    # Structural hierarchy: changes occasionally.
    if path.endswith(("/space", "/folder", "/list", "/field", "/custom_item")):
        return 300
    # Everything task-shaped changes constantly; cache only enough to absorb
    # the model re-reading the same page twice in a row.
    return 30


class ClickUpClient:
    """Owns the shared connection pool and the per-grant caches.

    One httpx client is shared across all users — only the Authorization header,
    the cache namespace and the rate-limit view are per-user.
    """

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=CLICKUP_API_BASE, timeout=HTTP_TIMEOUT)
        # grant_id -> {cache_key: (result, expires_at_monotonic)}
        self._caches: dict[int, dict[tuple, tuple[Any, float]]] = {}
        # grant_id -> {"remaining": int, "limit": int, "reset": int}
        self._rate_state: dict[int, dict[str, int]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    def for_grant(self, grant: ClickUpGrant) -> ScopedClickUpClient:
        return ScopedClickUpClient(self, grant)

    def rate_state(self, grant_id: int) -> dict[str, int]:
        return dict(self._rate_state.get(grant_id, {}))

    def forget_grant(self, grant_id: int) -> None:
        self._caches.pop(grant_id, None)
        self._rate_state.pop(grant_id, None)

    # --- cache, always namespaced by grant ----------------------------------

    def _cache_get(self, grant_id: int, key: tuple) -> Any | None:
        entry = self._caches.get(grant_id, {}).get(key)
        if entry is None:
            return None
        result, expires_at = entry
        if time.monotonic() >= expires_at:
            self._caches[grant_id].pop(key, None)
            return None
        return result

    def _cache_set(self, grant_id: int, key: tuple, result: Any, ttl: int) -> None:
        if ttl <= 0:
            return
        bucket = self._caches.setdefault(grant_id, {})
        bucket[key] = (result, time.monotonic() + ttl)
        if len(bucket) > MAX_CACHE_ENTRIES_PER_GRANT:
            oldest = min(bucket, key=lambda k: bucket[k][1])
            bucket.pop(oldest, None)

    def _cache_clear(self, grant_id: int) -> None:
        """Coarse but safe: any write by this user drops all of their cached reads."""
        self._caches.pop(grant_id, None)


class ScopedClickUpClient:
    """A ClickUpClient bound to one user's grant. Tools only ever see this."""

    __slots__ = ("_owner", "_grant")

    def __init__(self, owner: ClickUpClient, grant: ClickUpGrant):
        self._owner = owner
        self._grant = grant

    @property
    def grant(self) -> ClickUpGrant:
        return self._grant

    @property
    def workspaces(self) -> list[dict[str, Any]]:
        return self._grant.workspaces

    def default_team_id(self) -> str | None:
        """The caller's Workspace id when they have exactly one, else None."""
        if len(self._grant.workspaces) == 1:
            return self._grant.workspaces[0].get("id")
        return None

    # --- verbs --------------------------------------------------------------

    async def get(
        self, path: str, params: dict[str, Any] | None = None, force_refresh: bool = False
    ) -> Any:
        return await self._request("GET", path, params=params, force_refresh=force_refresh)

    async def post(
        self, path: str, json_body: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("POST", path, params=params, json_body=json_body)

    async def put(
        self, path: str, json_body: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("PUT", path, params=params, json_body=json_body)

    async def patch(
        self, path: str, json_body: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self._request("PATCH", path, params=params, json_body=json_body)

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any] | None = None
    ) -> Any:
        """Attachment upload — the only non-JSON endpoint in the API."""
        return await self._request("POST", path, files=files, form_data=data)

    # --- request path -------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        # OAuth tokens take the Bearer prefix; personal `pk_` tokens do not.
        return {
            "Authorization": f"Bearer {self._grant.access_token}",
            "Accept": "application/json",
        }

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: Any,
        files: dict[str, Any] | None,
        form_data: dict[str, Any] | None,
    ) -> httpx.Response:
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "params": params or None,
        }
        if files is not None:
            kwargs["files"] = files
            if form_data:
                kwargs["data"] = form_data
        elif json_body is not None:
            kwargs["json"] = json_body
        return await self._owner._http.request(method, path, **kwargs)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        files: dict[str, Any] | None = None,
        form_data: dict[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> Any:
        grant_id = self._grant.id
        params = {k: v for k, v in (params or {}).items() if v is not None}

        ttl = _cache_ttl_for(method, path)
        cache_key = (path, tuple(sorted(params.items())))
        if ttl > 0 and not force_refresh:
            cached = self._owner._cache_get(grant_id, cache_key)
            if cached is not None:
                logger.info(
                    "Cache hit", extra={"path": path, "grant_id": grant_id}
                )
                return cached

        response = await self._send(method, path, params, json_body, files, form_data)

        if response.status_code == 429:
            retry_after = self._retry_after(response)
            if retry_after is not None and retry_after <= 30:
                logger.warning(
                    "ClickUp 429; sleeping then retrying once",
                    extra={"path": path, "grant_id": grant_id, "retry_after": retry_after},
                )
                await asyncio.sleep(retry_after)
                response = await self._send(method, path, params, json_body, files, form_data)
            if response.status_code == 429:
                raise ClickUpRateLimitError(retry_after_seconds=self._retry_after(response) or 60)

        if response.status_code in RETRYABLE_STATUS:
            logger.warning(
                "ClickUp transient 5xx; retrying once after 1s",
                extra={"path": path, "grant_id": grant_id, "status": response.status_code},
            )
            await asyncio.sleep(1)
            response = await self._send(method, path, params, json_body, files, form_data)

        self._record_rate_headers(response)
        result = self._parse(response, path)

        if method != "GET":
            # A write may invalidate anything this user has cached.
            self._owner._cache_clear(grant_id)
        else:
            self._owner._cache_set(grant_id, cache_key, result, ttl)
        return result

    def _parse(self, response: httpx.Response, path: str) -> Any:
        if response.status_code >= 400:
            detail, ecode = self._error_detail(response)
            if response.status_code in (401, 403) and self._looks_like_bad_token(ecode, detail):
                raise ClickUpAuthError(
                    "ClickUp rejected this user's token — the integration was most likely "
                    "revoked in ClickUp. The user needs to reconnect. "
                    f"(ClickUp said: {detail})"
                )
            logger.warning(
                "ClickUp API error",
                extra={
                    "path": path,
                    "status": response.status_code,
                    "ecode": ecode,
                    "grant_id": self._grant.id,
                },
            )
            raise ClickUpError(response.status_code, detail, ecode)

        if not response.content:
            return {}
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return {"raw": response.text}

    @staticmethod
    def _error_detail(response: httpx.Response) -> tuple[str, str | None]:
        """ClickUp errors look like {"err": "...", "ECODE": "OAUTH_019"}."""
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            return response.text or f"HTTP {response.status_code}", None
        if isinstance(body, dict):
            detail = body.get("err") or body.get("error") or body.get("message")
            if detail is None:
                detail = json.dumps(body)
            return str(detail), body.get("ECODE")
        return str(body), None

    @staticmethod
    def _looks_like_bad_token(ecode: str | None, detail: str) -> bool:
        """Distinguish "your token is dead" from "you may not touch this object".

        ClickUp uses 401 for both a revoked token and an unauthorized resource, so
        a blanket re-auth prompt on every 401 would be misleading. OAUTH_0xx codes
        and the stock token messages are the reliable signals.
        """
        if ecode and ecode.upper().startswith("OAUTH_"):
            return True
        lowered = detail.lower()
        return any(
            phrase in lowered
            for phrase in ("token invalid", "token not found", "authorization header", "oauth")
        )

    def _record_rate_headers(self, response: httpx.Response) -> None:
        headers = response.headers
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is None:
            return
        try:
            state = {
                "remaining": int(remaining),
                "limit": int(headers.get("X-RateLimit-Limit") or 0),
                "reset": int(headers.get("X-RateLimit-Reset") or 0),
            }
        except ValueError:
            return
        self._owner._rate_state[self._grant.id] = state
        if state["remaining"] <= 10:
            logger.warning(
                "ClickUp rate limit approaching",
                extra={"grant_id": self._grant.id, **state},
            )

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return max(0, int(raw))
            except ValueError:
                pass
        # ClickUp prefers X-RateLimit-Reset, a Unix timestamp.
        reset = response.headers.get("X-RateLimit-Reset")
        if reset is not None:
            try:
                return max(0, int(float(reset) - time.time()))
            except ValueError:
                return None
        return None
