"""App Store Connect JWT minting. Source: docs/interfaces/asc-api.md #Authentication.

ES256-signed JWT from a PKCS#8 .p8 key. Cached with a 15-minute TTL (20-minute Apple ceiling
minus a 5-minute refresh margin) so a long publish run across many locales never hits a mid-run
401 from an expiring token.
"""

from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

from asogenic.config import ASCCredentials
from asogenic.errors import asc_auth_error
from asogenic.logging_config import get_logger

logger = get_logger(__name__)

TOKEN_LIFETIME_SECONDS = 1200  # Apple's 20-minute ceiling
REFRESH_MARGIN_SECONDS = 300  # 5 minutes
CACHE_TTL_SECONDS = TOKEN_LIFETIME_SECONDS - REFRESH_MARGIN_SECONDS  # 15 minutes


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _load_private_key(p8_pem: str) -> ec.EllipticCurvePrivateKey:
    try:
        key = serialization.load_pem_private_key(p8_pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise asc_auth_error(f"could not parse ASC .p8 private key: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise asc_auth_error("ASC .p8 key is not an EC private key")
    return key


def mint_jwt(creds: ASCCredentials, *, now: int | None = None) -> str:
    """Mint a fresh ES256 JWT for App Store Connect. Callers should use ASCTokenCache instead
    of calling this directly, to respect the 15-minute cache TTL."""
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + TOKEN_LIFETIME_SECONDS

    header = {"alg": "ES256", "kid": creds.key_id, "typ": "JWT"}
    payload = {
        "iss": creds.issuer_id,
        "iat": issued_at,
        "exp": expires_at,
        "aud": "appstoreconnect-v1",
    }

    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )

    private_key = _load_private_key(creds.p8_pem)
    der_signature = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = asym_utils.decode_dss_signature(der_signature)
    # ES256 JWS signatures are raw r||s, each a fixed 32-byte big-endian integer (P-256 curve).
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{signing_input}.{_b64url(raw_signature)}"


class ASCTokenCache:
    """Caches a minted JWT for CACHE_TTL_SECONDS, per docs/interfaces/asc-api.md."""

    def __init__(self, creds: ASCCredentials) -> None:
        self._creds = creds
        self._token: str | None = None
        self._minted_at: float = 0.0

    def get_token(self, *, now: float | None = None) -> str:
        clock = time.time() if now is None else now
        if self._token is None or (clock - self._minted_at) >= CACHE_TTL_SECONDS:
            logger.info("minting fresh ASC JWT (key_id=%s)", self._creds.key_id)
            self._token = mint_jwt(self._creds, now=int(clock))
            self._minted_at = clock
        return self._token

    def invalidate(self) -> None:
        logger.info("invalidating cached ASC JWT (key_id=%s)", self._creds.key_id)
        self._token = None
        self._minted_at = 0.0
