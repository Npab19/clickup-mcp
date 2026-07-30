"""OAuth 2.1 authorization server that delegates user authentication to ClickUp.

Structurally this follows the Whoop provider, with three deliberate differences:

1. **No PKCE toward ClickUp.** ClickUp's authorize endpoint does not support it.
   PKCE between the MCP client and this server is untouched — that is what the
   `code_challenge` carried through `pending_auth` is for.
2. **No scopes toward ClickUp.** ClickUp has no scope parameter; the user chooses
   which Workspaces to grant on the consent screen.
3. **Every MCP token is bound to a ClickUp grant.** This is what makes the server
   multi-user. Whoop's provider hands its upstream tokens to a global singleton;
   here the ClickUp token is looked up per request from the caller's MCP token.
   The binding has to survive `exchange_refresh_token` too, or refreshing an MCP
   token would silently de-authorize the user.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from pydantic import AnyHttpUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from clickup_mcp.constants import (
    AUTH_CODE_TTL,
    CALLBACK_PATH,
    CLICKUP_API_BASE,
    CLICKUP_AUTHORIZE_URL,
    CLICKUP_TOKEN_URL,
    HTTP_TIMEOUT,
    MCP_ACCESS_TOKEN_TTL,
    MCP_SCOPE,
    PENDING_AUTH_TTL,
)
from clickup_mcp.store import Store

logger = logging.getLogger(__name__)


class ClickUpOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, server_url: str, store: Store, client_id: str, client_secret: str):
        self._server_url = server_url.rstrip("/")
        self._store = store
        self._clickup_client_id = client_id
        self._clickup_client_secret = client_secret

    @property
    def redirect_uri(self) -> str:
        return f"{self._server_url}{CALLBACK_PATH}"

    # --- MCP client registration --------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = await self._store.get_client(client_id)
        if raw is None:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            client_info.client_id = f"client_{secrets.token_hex(16)}"
            client_info.client_id_issued_at = int(time.time())
        await self._store.put_client(client_info.client_id, client_info.model_dump_json())
        # Log only a prefix — the full client_id is a credential.
        logger.info(
            "Registered MCP client",
            extra={"client_id_prefix": client_info.client_id[:12]},
        )

    # --- authorization ------------------------------------------------------

    @staticmethod
    def _effective_scopes(
        client: OAuthClientInformationFull, requested: list[str] | None
    ) -> list[str]:
        """Resolve the scopes to grant when the client asked for none.

        `OAuthClientInformationFull.validate_scope(None)` returns None, so a client
        that omits `scope` from the authorization request reaches us with no scopes
        at all. Storing that verbatim mints an access token with `scopes=[]`, which
        can never satisfy `required_scopes` — the OAuth dance completes, the client
        reports success, and then every request to /mcp is rejected 403
        insufficient_scope. That looks exactly like "authenticated, but the server
        says needs-auth".

        `ClientRegistrationOptions.default_scopes` does not help here: it only fills
        in the client's registered `scope` at registration time and is never
        consulted during authorization.

        Clients differ on this — Claude's web client sends `scope`, the VS Code
        client does not — so fall back to what the client registered with, then to
        the server's own scope.
        """
        if requested:
            return requested
        if client.scope:
            return client.scope.split()
        return [MCP_SCOPE]

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Send the user to ClickUp rather than rendering a login form."""
        state = secrets.token_urlsafe(32)

        await self._store.put_pending(
            state,
            {
                "redirect_uri": str(params.redirect_uri),
                "code_challenge": params.code_challenge,
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "client_id": client.client_id,
                "scopes": self._effective_scopes(client, params.scopes),
                "original_state": params.state,
                "resource": params.resource,
            },
            ttl=PENDING_AUTH_TTL,
        )

        query = urllib.parse.urlencode(
            {
                "client_id": self._clickup_client_id,
                "redirect_uri": self.redirect_uri,
                "state": state,
            }
        )
        logger.info("Redirecting user to ClickUp for authorization")
        return f"{CLICKUP_AUTHORIZE_URL}?{query}"

    async def handle_clickup_callback(self, code: str, state: str) -> str:
        """Exchange ClickUp's code, identify the user, and resume the MCP flow."""
        pending = await self._store.take_pending(state)
        if not pending:
            raise ValueError(
                "Invalid or expired state parameter. Start the authorization again."
            )

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
            response = await http.post(
                CLICKUP_TOKEN_URL,
                json={
                    "client_id": self._clickup_client_id,
                    "client_secret": self._clickup_client_secret,
                    "code": code,
                },
            )
            if response.status_code != 200:
                logger.error(
                    "ClickUp token exchange failed",
                    extra={"status": response.status_code},
                )
                raise ValueError("ClickUp token exchange failed")

            payload = response.json()
            access_token = payload.get("access_token")
            if not access_token:
                logger.error("ClickUp token response had no access_token")
                raise ValueError("ClickUp did not return an access token")

            identity = await self._fetch_identity(http, access_token)
            workspaces = await self._fetch_workspaces(http, access_token)

        grant_id = await self._store.upsert_grant(
            clickup_user_id=identity["id"],
            email=identity.get("email"),
            username=identity.get("username"),
            access_token=access_token,
            workspaces=workspaces,
        )
        logger.info(
            "ClickUp authorization stored",
            extra={
                "clickup_user_id": identity["id"],
                "grant_id": grant_id,
                "workspace_count": len(workspaces),
            },
        )

        mcp_code = f"mcp_{secrets.token_urlsafe(32)}"
        expires_at = time.time() + AUTH_CODE_TTL
        auth_code = AuthorizationCode(
            code=mcp_code,
            client_id=pending["client_id"],
            redirect_uri=AnyHttpUrl(pending["redirect_uri"]),
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            expires_at=expires_at,
            scopes=pending.get("scopes") or [],
            code_challenge=pending["code_challenge"],
            resource=pending.get("resource"),
        )
        await self._store.put_auth_code(
            code=mcp_code,
            client_id=pending["client_id"],
            grant_id=grant_id,
            payload=auth_code.model_dump_json(),
            expires_at=expires_at,
        )

        return construct_redirect_uri(
            pending["redirect_uri"],
            code=mcp_code,
            state=pending.get("original_state"),
        )

    @staticmethod
    async def _fetch_identity(http: httpx.AsyncClient, token: str) -> dict[str, Any]:
        """Identify the ClickUp user behind a freshly-issued token.

        This is what the grant is keyed on, so a failure here has to be fatal —
        an unidentified grant could not be deduplicated or audited.
        """
        response = await http.get(
            f"{CLICKUP_API_BASE}/v2/user",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            logger.error(
                "Could not identify ClickUp user", extra={"status": response.status_code}
            )
            raise ValueError("Could not read the ClickUp user profile for this token")
        user = (response.json() or {}).get("user") or {}
        if user.get("id") is None:
            raise ValueError("ClickUp user profile had no id")
        return {
            "id": str(user["id"]),
            "email": user.get("email"),
            "username": user.get("username"),
        }

    @staticmethod
    async def _fetch_workspaces(http: httpx.AsyncClient, token: str) -> list[dict[str, Any]]:
        """Cache the authorized Workspaces on the grant.

        Nearly every v2 endpoint needs a `team_id`, and a user with exactly one
        Workspace should not have to look it up. Non-fatal: an empty list just
        means tools will ask for `team_id` explicitly.
        """
        try:
            response = await http.get(
                f"{CLICKUP_API_BASE}/v2/team",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                return []
            teams = (response.json() or {}).get("teams") or []
            return [{"id": str(t.get("id")), "name": t.get("name")} for t in teams]
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            logger.warning("Could not pre-fetch ClickUp workspaces", exc_info=True)
            return []

    # --- code / token exchange ----------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = await self._store.get_auth_code(authorization_code)
        if record is None:
            return None
        client_id, _grant_id, payload = record
        if client_id != client.client_id:
            return None
        return AuthorizationCode.model_validate_json(payload)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        record = await self._store.get_auth_code(authorization_code.code)
        if record is None:
            raise TokenError(
                error="invalid_grant", error_description="Invalid or expired authorization code"
            )
        _client_id, grant_id, _payload = record

        token = await self._issue_token_pair(
            client_id=client.client_id or "",
            grant_id=grant_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )
        await self._store.delete_auth_code(authorization_code.code)
        return token

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        record = await self._store.get_refresh_token(refresh_token)
        if record is None or record["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=record["scopes"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        record = await self._store.get_refresh_token(refresh_token.token)
        if record is None:
            raise TokenError(
                error="invalid_grant", error_description="Invalid or expired refresh token"
            )

        # Carrying grant_id onto the new pair is the whole point — drop it here and
        # the user's next tool call finds no ClickUp token behind their MCP token.
        token = await self._issue_token_pair(
            client_id=client.client_id or "",
            grant_id=record["grant_id"],
            scopes=scopes or refresh_token.scopes,
            resource=None,
        )
        await self._store.delete_refresh_token(refresh_token.token)
        return token

    async def _issue_token_pair(
        self,
        client_id: str,
        grant_id: int,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        # Belt and braces: an access token with no scopes cannot satisfy
        # `required_scopes`, so it would authenticate and then fail every call.
        # Nothing should reach here with an empty list, but if it does, a usable
        # token beats a silently broken one.
        if not scopes:
            logger.warning(
                "Minting a token with no requested scopes; defaulting to the server scope"
            )
            scopes = [MCP_SCOPE]

        access = f"mcp_{secrets.token_urlsafe(32)}"
        refresh = f"mcpr_{secrets.token_urlsafe(32)}"

        await self._store.put_access_token(
            token=access,
            client_id=client_id,
            grant_id=grant_id,
            scopes=scopes,
            expires_at=time.time() + MCP_ACCESS_TOKEN_TTL,
            resource=str(resource) if resource else None,
        )
        await self._store.put_refresh_token(
            token=refresh, client_id=client_id, grant_id=grant_id, scopes=scopes
        )

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=MCP_ACCESS_TOKEN_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    # --- verification -------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        record = await self._store.get_access_token(token)
        if record is None:
            return None
        return AccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=int(record["expires_at"]) if record["expires_at"] else None,
            resource=record["resource"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            await self._store.delete_access_token(token.token)
        else:
            await self._store.delete_refresh_token(token.token)
