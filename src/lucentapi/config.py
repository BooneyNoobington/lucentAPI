"""Settings from the environment (prefix `LUCENT_`) or a `.env` file."""

import os
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROLES = ("sampler", "analyst", "reviewer", "admin")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCENT_", env_file=".env", extra="forbid")

    # SQLAlchemy URL, e.g. mysql+pymysql://lucentapi:…@db.example.org/lucent?charset=utf8mb4
    database_url: str

    # OpenID Connect: tokens must come from this issuer and name this audience.
    oidc_issuer: str
    oidc_audience: str
    # Where the signing keys are. Without either, they are found through the issuer's
    # /.well-known/openid-configuration.
    oidc_jwks_url: str | None = None
    oidc_jwks_file: Path | None = None
    oidc_leeway_seconds: int = 30

    # Which claim names the lucentBASE person (matched against person.unix_account) and which
    # claim holds the roles. Dots walk into nested claims, e.g. Keycloak's realm_access.roles.
    username_claim: str = "preferred_username"
    roles_claim: str = "roles"
    # Maps names in the roles claim to API roles, e.g. {"lims-reviewers": "reviewer"}. Names that
    # are API roles themselves (sampler, analyst, reviewer, admin) always count.
    role_map: dict[str, str] = {}

    # Collection identifiers are the numbering scheme's identifier for id_collection + offset.
    numbering_offset: int = 0

    @model_validator(mode="after")
    def check(self) -> Self:
        # Unknown keys in .env are refused by extra="forbid"; unknown environment variables
        # would be ignored, so a typo there must stop the start too.
        known = {f"LUCENT_{name.upper()}" for name in type(self).model_fields}
        unknown = sorted(key for key in os.environ if key.upper().startswith("LUCENT_") and key.upper() not in known)
        if unknown:
            raise ValueError(f"unknown settings in the environment: {unknown}")
        if self.oidc_jwks_url and self.oidc_jwks_file:
            raise ValueError("set LUCENT_OIDC_JWKS_URL or LUCENT_OIDC_JWKS_FILE, not both")
        unknown = sorted(set(self.role_map.values()) - set(ROLES))
        if unknown:
            raise ValueError(f"LUCENT_ROLE_MAP maps to unknown roles {unknown}; roles are {list(ROLES)}")
        return self
