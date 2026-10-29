"""Bearer tokens from an OpenID Connect provider.

The API doesn't handle passwords. Clients log in at the identity provider (Keycloak, Authentik,
Entra ID, …) and send the access token they get. The API checks its signature against the
provider's published keys (JWKS), its issuer, audience and expiry, and then looks up the
lucentBASE person whose `unix_account` equals the token's username claim. Every change in the
database is attributed to that person, so machine clients (instrument imports) need a person
row too.
"""

import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from .config import ROLES, Settings
from .db import visible
from .errors import APIError
from .schema import person

ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


@dataclass(frozen=True)
class Caller:
    person_id: int
    username: str
    roles: frozenset[str]

    def has(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


class KeySet:
    """The provider's signing keys, refreshed when a token names a key we don't know."""

    def __init__(self, settings: Settings, refresh_seconds: int = 300):
        self.settings = settings
        self.refresh_seconds = refresh_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    def key(self, kid: str | None) -> jwt.PyJWK:
        with self._lock:
            if kid not in self._keys and time.monotonic() - self._fetched > 10:
                self._load()
            elif time.monotonic() - self._fetched > self.refresh_seconds:
                self._load()
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        if kid not in self._keys:
            raise APIError(401, "invalid_token", "The token is signed with an unknown key.", headers=challenge("invalid_token"))
        return self._keys[kid]

    def _load(self) -> None:
        if self.settings.oidc_jwks_file:
            document = json.loads(self.settings.oidc_jwks_file.read_text())
        else:
            document = _fetch_json(self.settings.oidc_jwks_url or self._discover())
        keys = jwt.PyJWKSet.from_dict(document).keys
        self._keys = {key.key_id: key for key in keys}
        self._fetched = time.monotonic()

    def _discover(self) -> str:
        configuration = _fetch_json(self.settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration")
        return configuration["jwks_uri"]


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 (https URL from the settings)
        return json.load(response)


def challenge(error: str | None = None) -> dict:
    return {"WWW-Authenticate": f'Bearer error="{error}"' if error else "Bearer"}


def claim(claims: dict, path: str):
    """A claim by dotted path, e.g. realm_access.roles."""
    current = claims
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def verify(token: str, settings: Settings, keys: KeySet) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        key = keys.key(header.get("kid"))
        return jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.oidc_leeway_seconds,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise APIError(401, "token_expired", "The token has expired.", headers=challenge("invalid_token"))
    except jwt.PyJWTError as e:
        raise APIError(401, "invalid_token", f"The token is not valid: {e}", headers=challenge("invalid_token"))


def roles_from(claims: dict, settings: Settings) -> frozenset[str]:
    names = claim(claims, settings.roles_claim) or []
    if isinstance(names, str):
        names = names.split()
    roles = {settings.role_map.get(name, name) for name in names}
    return frozenset(roles & set(ROLES))


bearer = HTTPBearer(auto_error=False, description="An access token from the OpenID Connect provider.")


def current_caller(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> Caller:
    if credentials is None:
        raise APIError(401, "missing_token", "Send an access token: Authorization: Bearer <token>.", headers=challenge())
    settings: Settings = request.app.state.settings
    claims = verify(credentials.credentials, settings, request.app.state.keys)

    username = claim(claims, settings.username_claim)
    if not isinstance(username, str) or not username:
        raise APIError(401, "invalid_token", f"The token has no {settings.username_claim} claim.", headers=challenge("invalid_token"))
    with request.app.state.engine.connect() as conn:
        person_id = conn.scalar(
            select(person.c.id_person).where(person.c.unix_account == username, visible(person))
        )
    if person_id is None:
        raise APIError(403, "unknown_person", f"No person in lucentBASE has the account {username!r}.")
    caller = Caller(person_id=person_id, username=username, roles=roles_from(claims, settings))
    request.state.caller = caller
    return caller


CurrentCaller = Annotated[Caller, Depends(current_caller)]


def require(role: str):
    """A dependency that lets only callers with `role` (or admin) through."""

    def check(caller: CurrentCaller) -> Caller:
        if not caller.has(role):
            raise APIError(403, "missing_role", f"This needs the role {role!r}.")
        return caller

    return check
