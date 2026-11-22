"""Signed, short-lived instructions for client-executed public Apple research."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from uuid import uuid4

from asogenic.aso.storefront_catalog import mapping_for_locale
from asogenic.aso.storefront_ids import storefront_id_for_country
from asogenic.errors import validation_error

SCHEMA = "asogenic.local-research-plan"
VERSION = 1
SIGNATURE_FIELD = "signature"
ALLOWED_APPLE_HOSTS = ["itunes.apple.com", "search.itunes.apple.com"]
MAX_HINT_SEED_REQUESTS = 8
MAX_ITUNES_SEARCHES = 12
MAX_CONCURRENT_REQUESTS = 4

# Keep a process-stable fallback for development. Production deployments must configure the
# environment secret so plans remain verifiable across workers and restarts.
_EPHEMERAL_SECRET = secrets.token_bytes(32)


def _secret() -> bytes:
    configured = os.environ.get("ASOGENIC_LOCAL_RESEARCH_PLAN_SECRET")
    return configured.encode("utf-8") if configured else _EPHEMERAL_SECRET


def _fail(message: str, field: str = "plan", code: str = "INVALID_RESEARCH_PLAN") -> NoReturn:
    raise validation_error(message, field_name=field, code=code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("datetime must include a timezone", "now")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _valid_str(value: Any, field: str, *, allow_empty: bool = False) -> bool:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"{field} must be {'a string' if allow_empty else 'a non-empty string'}", field)
    return True


def _signature(plan: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in plan.items() if key != SIGNATURE_FIELD}
    return hmac.new(_secret(), _canonical(unsigned), hashlib.sha256).hexdigest()


def issue_research_plan(
    *, tenant_id: str, bundle_id: str, locale: str, accepted_seeds: list[str],
    target_genre_id: int | None, brand_name: str | None, excluded_brand_terms: list[str],
    allow_generic_seeds: bool, request_fingerprint: str, now: datetime | None = None,
    ttl_seconds: int = 900,
) -> dict[str, Any]:
    """Create an immutable request plan signed for the issuing tenant."""
    for name, value in (("tenant_id", tenant_id), ("bundle_id", bundle_id),
                        ("locale", locale), ("request_fingerprint", request_fingerprint)):
        _valid_str(value, name)
    if not isinstance(accepted_seeds, list) or len(accepted_seeds) > MAX_HINT_SEED_REQUESTS:
        _fail(f"accepted_seeds must be a list of at most {MAX_HINT_SEED_REQUESTS} strings", "accepted_seeds")
    if any(not isinstance(seed, str) or not seed.strip() for seed in accepted_seeds):
        _fail("accepted_seeds must contain only non-empty strings", "accepted_seeds")
    if not isinstance(excluded_brand_terms, list) or any(
        not isinstance(term, str) or not term.strip() for term in excluded_brand_terms
    ):
        _fail("excluded_brand_terms must be a list of non-empty strings", "excluded_brand_terms")
    if target_genre_id is not None and (isinstance(target_genre_id, bool) or not isinstance(target_genre_id, int)):
        _fail("target_genre_id must be an integer or null", "target_genre_id")
    if brand_name is not None and not isinstance(brand_name, str):
        _fail("brand_name must be a string or null", "brand_name")
    if not isinstance(allow_generic_seeds, bool):
        _fail("allow_generic_seeds must be a boolean", "allow_generic_seeds")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 3600:
        _fail("ttl_seconds must be between 1 and 3600", "ttl_seconds")

    issued = now or datetime.now(UTC)
    issued_at = _timestamp(issued)
    expires_at = _timestamp(issued + timedelta(seconds=ttl_seconds))
    storefront = mapping_for_locale(locale)
    country_code = storefront.country_code if storefront is not None else None
    storefront_id = storefront_id_for_country(country_code) if country_code else None
    payload = {
        "tenant_id": tenant_id,
        "bundle_id": bundle_id,
        "locale": locale,
        "accepted_seeds": list(accepted_seeds),
        "target_genre_id": target_genre_id,
        "brand_name": brand_name,
        "excluded_brand_terms": list(excluded_brand_terms),
        "allow_generic_seeds": allow_generic_seeds,
        "request_fingerprint": request_fingerprint,
    }
    plan: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "plan_id": str(uuid4()),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "request": payload,
        "instructions": {
            "purpose": "Execute only the public Apple research requests needed for this plan, then return raw responses as evidence.",
            "allowed_hosts": list(ALLOWED_APPLE_HOSTS),
            "forbidden": ["Do not use App Store Connect credentials or access private/account data."],
            "limits": {
                "maximum_hints_seed_requests": MAX_HINT_SEED_REQUESTS,
                "maximum_itunes_searches": MAX_ITUNES_SEARCHES,
                "maximum_concurrent_requests": MAX_CONCURRENT_REQUESTS,
                "pacing": "Use conservative sequential pacing, at least 3 seconds between requests; back off on 429/403 and stop if throttling persists.",
            },
            "operations": [
                {
                    "id": "autocomplete",
                    "method": "GET",
                    "url": "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints",
                    "for_each": "accepted_seeds only",
                    "query": {"clientApplication": "Software", "term": "<seed>"},
                    "headers": (
                        {"X-Apple-Store-Front": str(storefront_id)}
                        if storefront_id is not None else {}
                    ),
                    "response_evidence": (
                        "Decode Apple's plist response and store only its hints as "
                        "evidence.hints[seed] = {hints: [{term: string}, ...]}."
                    ),
                },
                {
                    "id": "itunes_search",
                    "method": "GET",
                    "url": "https://itunes.apple.com/search",
                    "for_each": "up to 12 candidate terms from the seed autocomplete responses",
                    "query": {
                        "term": "<candidate term>",
                        "country": country_code,
                        "entity": "software",
                        "limit": 50,
                    },
                    "response_evidence": (
                        "Store the response-derived resultCount/results fields as "
                        "evidence.searches[term]. Keep only fields required by the plan."
                    ),
                },
            ],
            "evidence": (
                "Return bounded, response-derived evidence only. Do not invent terms or listings; "
                "the server validates it and reruns deterministic scoring."
            ),
        },
    }
    plan[SIGNATURE_FIELD] = _signature(plan)
    return plan


def verify_research_plan(
    plan: dict[str, Any], *, tenant_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Validate a plan and return a copy without its signature."""
    if not isinstance(plan, dict):
        _fail("plan must be an object")
    _valid_str(tenant_id, "tenant_id")
    signature = plan.get(SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        _fail("plan is missing its signature", SIGNATURE_FIELD, "MISSING_SIGNATURE")
    if plan.get("schema") != SCHEMA or plan.get("version") != VERSION:
        _fail("unsupported research plan schema or version", "schema")
    required = {"schema", "version", "plan_id", "issued_at", "expires_at", "request", "instructions", SIGNATURE_FIELD}
    if set(plan) != required:
        _fail("plan has missing or unexpected fields")
    if not hmac.compare_digest(signature, _signature(plan)):
        _fail("plan signature does not match its contents", SIGNATURE_FIELD, "INVALID_SIGNATURE")
    request = plan.get("request")
    if not isinstance(request, dict):
        _fail("request must be an object", "request")
    for field in ("tenant_id", "bundle_id", "locale", "request_fingerprint"):
        _valid_str(request.get(field), f"request.{field}")
    if request["tenant_id"] != tenant_id:
        _fail("plan belongs to a different tenant", "tenant_id", "TENANT_MISMATCH")
    if not isinstance(request.get("accepted_seeds"), list) or len(request["accepted_seeds"]) > MAX_HINT_SEED_REQUESTS:
        _fail("accepted_seeds exceeds the allowed bound", "request.accepted_seeds")
    if any(not isinstance(item, str) or not item.strip() for item in request["accepted_seeds"]):
        _fail("accepted_seeds contains malformed values", "request.accepted_seeds")
    if not isinstance(request.get("excluded_brand_terms"), list) or any(
        not isinstance(item, str) for item in request["excluded_brand_terms"]
    ):
        _fail("excluded_brand_terms must be a list of strings", "request.excluded_brand_terms")
    if request.get("target_genre_id") is not None and (
        isinstance(request["target_genre_id"], bool) or not isinstance(request["target_genre_id"], int)
    ):
        _fail("target_genre_id must be an integer or null", "request.target_genre_id")
    if request.get("brand_name") is not None and not isinstance(request["brand_name"], str):
        _fail("brand_name must be a string or null", "request.brand_name")
    if not isinstance(request.get("allow_generic_seeds"), bool):
        _fail("allow_generic_seeds must be a boolean", "request.allow_generic_seeds")
    if not isinstance(plan.get("plan_id"), str) or not plan["plan_id"]:
        _fail("plan_id must be a non-empty string", "plan_id")
    try:
        issued = datetime.fromisoformat(plan["issued_at"])
        expires = datetime.fromisoformat(plan["expires_at"])
    except (AttributeError, TypeError, ValueError):
        _fail("issued_at and expires_at must be ISO timestamps", "expires_at")
    if issued.tzinfo is None or expires.tzinfo is None or expires <= issued:
        _fail("plan timestamps are invalid", "expires_at")
    current = now or datetime.now(UTC)
    _timestamp(current)
    if current.astimezone(UTC) >= expires.astimezone(UTC):
        _fail("research plan has expired", "expires_at", "EXPIRED_RESEARCH_PLAN")
    instructions = plan.get("instructions")
    if not isinstance(instructions, dict) or instructions.get("allowed_hosts") != ALLOWED_APPLE_HOSTS:
        _fail("plan instructions are malformed", "instructions")
    return {key: value for key, value in plan.items() if key != SIGNATURE_FIELD}
