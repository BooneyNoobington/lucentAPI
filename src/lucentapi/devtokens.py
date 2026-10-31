"""Tokens for development and tests, signed with a local key instead of an identity provider.

Point `LUCENT_OIDC_JWKS_FILE` at the `jwks.json` this writes, and the API accepts the tokens.
Never configure a production instance this way.
"""

import json
import time
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://dev.lucent.invalid"
AUDIENCE = "lucentapi"


class DevSigner:
    def __init__(self, directory: Path, kid: str = "dev"):
        self.kid = kid
        self.key_file = directory / ".keys" / "private.pem"
        self.jwks_file = directory / "jwks.json"
        if self.key_file.exists():
            self.key = serialization.load_pem_private_key(self.key_file.read_bytes(), password=None)
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            self.key_file.touch(mode=0o600)
            self.key_file.write_bytes(self.key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=kid, use="sig", alg="RS256")
        self.jwks_file.write_text(json.dumps({"keys": [jwk]}, indent=2) + "\n")

    def token(self, username: str, *, lifetime: int = 3600, issuer: str = ISSUER, audience: str = AUDIENCE,
              **claims) -> str:
        """A token for `username`. What they may do comes from lucentBASE, not from the token."""
        now = int(time.time())
        payload = {
            "iss": issuer, "aud": audience, "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, username)),
            "iat": now, "exp": now + lifetime, "preferred_username": username, **claims,
        }
        return jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": self.kid})
