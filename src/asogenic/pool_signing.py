"""HMAC signing for KeywordPool payloads that round-trip through the MCP client.

asogenic_assemble_locale/asogenic_list_brand_candidates trust the `pool` dict a client hands back
verbatim from asogenic_research_keywords/asogenic_optimize_source_search. Per the server's own
design principle ("the model never selects keywords — this does"), that guarantee only holds if
the pool is provably the server's own output. Signing it here and verifying on the way back in
is what stops an untrusted client from hand-crafting a pool to force a specific keyword outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from decimal import Decimal
from typing import Any

from asogenic.errors import validation_error
from asogenic.logging_config import get_logger
from asogenic.release import info as release_info

logger = get_logger(__name__)

SIGNATURE_KEY = "_signature"
SIGNATURE_VERSION_KEY = "_signature_version"
CANONICAL_VERSION = "pool-json-v1"
_POOL_FIELDS = frozenset({
    "locale_id", "terms", "grounding", "keyword_style", "category_name", "top_apps",
    "request_fingerprint", "seed_fingerprint", "cache_key_version",
    "server_build", "contract_version", "pool_signature_version",
})

_EPHEMERAL_SECRET = secrets.token_hex(32).encode("utf-8")
_warned_about_ephemeral_secret = False


def _secret() -> bytes:
    """Return the configured signing key at call time.

    The local stdio launcher installs its managed key during startup, which can
    happen after this module is imported. Reading the environment lazily keeps
    both local entrypoints correct without weakening hosted deployments.
    """
    configured = os.environ.get("ASOGENIC_POOL_SECRET")
    if configured:
        return configured.encode("utf-8")

    global _warned_about_ephemeral_secret
    if not _warned_about_ephemeral_secret:
        logger.warning(
            "ASOGENIC_POOL_SECRET not set - generated an ephemeral pool-signing secret for this "
            "process. Pools signed now will fail verification after a restart; set "
            "ASOGENIC_POOL_SECRET to a stable value for verification to survive a restart."
        )
        _warned_about_ephemeral_secret = True
    return _EPHEMERAL_SECRET


def _normalise(value: Any) -> Any:
    """Make JSON values stable across MCP implementations.

    In particular, JSON has one numeric type while Python distinguishes ``1`` and ``1.0``.
    Clients are allowed to parse and emit the returned object, so signing Python's incidental
    representation would make an unchanged pool unverifiable.
    """
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("pool contains a non-finite number")
        if number == number.to_integral_value():
            return int(number)
        return format(number.normalize(), "f")
    return str(value)


def _canonical(pool_dict: dict[str, Any]) -> bytes:
    # Sign only the pool contract. Diagnostic/cache metadata is deliberately outside the signed
    # payload so an MCP client can preserve, reorder, or omit observability fields without making
    # an otherwise unchanged producer pool unverifiable.
    payload = {k: v for k, v in pool_dict.items() if k in _POOL_FIELDS}
    return json.dumps(_normalise(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(pool_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy signed using the stable wire representation."""
    # Put the release identity inside the signed contract as well as in the surrounding
    # observability response. A client or connector that keeps a pool across deployments can
    # therefore be rejected explicitly instead of failing later with an opaque signature error.
    signed_pool = {**pool_dict, **release_info(), "pool_signature_version": CANONICAL_VERSION}
    signature = hmac.new(_secret(), _canonical(signed_pool), hashlib.sha256).hexdigest()
    return {**signed_pool, SIGNATURE_VERSION_KEY: CANONICAL_VERSION, SIGNATURE_KEY: signature}


def verify(pool_dict: dict[str, Any]) -> dict[str, Any]:
    """Returns pool_dict with the signature field stripped, once verified. Raises a typed
    validation error if the signature is missing or doesn't match the contents."""
    signature = pool_dict.get(SIGNATURE_KEY)
    if not signature:
        raise validation_error(
            "pool is missing its signature — pass the pool exactly as returned by "
            "asogenic_research_keywords/asogenic_optimize_source_search, unmodified",
            field_name="pool",
        )
    version = pool_dict.get(SIGNATURE_VERSION_KEY)
    if version != CANONICAL_VERSION:
        raise validation_error(
            f"pool signature version mismatch: received {version!r}, expected {CANONICAL_VERSION!r}; "
            "request a fresh pool from the producer",
            field_name="pool",
        )
    current = release_info()
    for field in ("server_build", "contract_version", "pool_signature_version"):
        received = pool_dict.get(field)
        expected = current[field] if field != "pool_signature_version" else CANONICAL_VERSION
        if received != expected:
            raise validation_error(
                f"pool belongs to build {pool_dict.get('server_build')!r}; current build is "
                f"{current['server_build']!r}. Request a fresh pool from the producer",
                field_name="pool",
                code="STALE_POOL",
                detail={"field": field, "received": received, "expected": expected},
            )
    expected = hmac.new(_secret(), _canonical(pool_dict), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(signature), expected):
        raise validation_error(
            f"pool signature does not match its contents (expected {expected[:12]}…, received {str(signature)[:12]}…); "
            "the pool contents changed or were serialized incompatibly after "
            "asogenic_research_keywords/asogenic_optimize_source_search produced it",
            field_name="pool",
        )
    return {k: v for k, v in pool_dict.items() if k not in (SIGNATURE_KEY, SIGNATURE_VERSION_KEY)}
