"""Phase 4 tools: Smart Localization (ASO) and source listing optimization. Source:
docs/interfaces/mcp-tools.md #Smart Localization / #Source listing optimization,
docs/use-cases/{06-smart-localization-aso,07-source-optimization}.md.

The client proposes wordings; these tools research, score, and assemble byte-exactly. The model
never selects keywords.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from asogenic import context, pool_signing
from asogenic.aso import (
    evidence_research,
    keyword_field_editor,
    local_research_plan,
    metadata_assembler,
)
from asogenic.aso.keyword_field_editor import Inserted
from asogenic.aso.storefront_catalog import KeywordFieldStyle
from asogenic.errors import validation_error
from asogenic.models.locales import is_valid_locale
from asogenic.models.metadata import MetadataLocalization
from asogenic.release import info as release_info
from asogenic.tools.field_spec import BARRED_CONTENT_RULES
from asogenic.tools.pool_serde import (
    coverage_report_to_dict,
    pool_from_dict,
    pool_to_dict,
    ranked_app_to_dict,
)

MAX_SEEDS = 25
_SUPPORTED_GENRE_IDS = range(6000, 6027)
_GENERIC_SEEDS = {"doctor", "reports", "tracker", "free", "app", "apps", "tool", "tools"}
_PLATFORM_SEEDS = {"app store", "ios", "iphone", "ipad", "apple", "aso", "appstore"}
_FEATURE_JARGON = {"metadata", "localization", "keywords", "screenshot", "api", "mcp"}
_NON_LATIN_GENERIC_SEEDS = {
    "ゲーム", "無料", "アプリ", "앱", "무료", "게임", "تطبيق", "مجاني", "لعبة",
}
_HEALTH_PATTERNS = {"diagnos", "symptom", "medical", "clinical", "doctor", "therapy", "treatment", "medication", "medicine", "glp-1", "glp1", "weight loss", "lose weight", "health", "wellness", "kymlo", "vona"}


def _validate_seed_count(seeds: list[str]) -> None:
    if len(seeds) > MAX_SEEDS:
        raise validation_error(
            f"too many seeds ({len(seeds)}); each seed fans out to multiple throttled network "
            f"calls, so the limit is {MAX_SEEDS} — narrow the seed list and call again",
            field_name="seeds",
            limit=MAX_SEEDS,
        )


def _validate_target_genre_id(target_genre_id: int | None) -> None:
    if target_genre_id is not None and (
        isinstance(target_genre_id, bool) or target_genre_id not in _SUPPORTED_GENRE_IDS
    ):
        raise validation_error(
            f"unsupported target_genre_id '{target_genre_id}'; expected an App Store genre ID from 6000 through 6026",
            field_name="target_genre_id",
        )


_SEEDING_GUIDANCE = (
    "seeds must be real end-user search phrases for THIS app's job-to-be-done: the "
    "2-4 word queries a user would actually type (\"menopause symptom tracker\", "
    "\"hrt reminder\", \"hot flashes diary\"). Do NOT seed with isolated generic words "
    "(\"doctor\", \"reports\", \"tracker\", \"free\"), the brand/app name, competitor "
    "names, or feature jargon no one searches — each seed fans out through Apple "
    "autocomplete, so an off-target seed pulls a whole branch of unrelated results "
    "into the pool. 4-12 well-chosen phrases beat 25 loose ones."
)


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _seed_validation(
    seeds: list[str], *, brand_name: str | None, excluded_brand_terms: list[str] | None,
    allow_generic_seeds: bool,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    brand_keys = {re.sub(r"[^\w]+", " ", value.casefold()).strip() for value in [brand_name or "", *(excluded_brand_terms or [])] if value.strip()}
    accepted: list[str] = []
    warnings: list[dict[str, str]] = []
    rejected: list[str] = []
    for raw in seeds:
        seed = raw.strip()
        key = re.sub(r"[^\w]+", " ", seed.casefold()).strip()
        if not key:
            continue
        if key in brand_keys:
            warnings.append({"seed": seed, "code": "brand_or_competitor", "reason": "brand/app or competitor names are not research seeds"})
            rejected.append(seed)
        elif key in _GENERIC_SEEDS or key in _PLATFORM_SEEDS or key in _FEATURE_JARGON or seed.casefold() in _NON_LATIN_GENERIC_SEEDS:
            if allow_generic_seeds:
                warnings.append({"seed": seed, "code": "low_confidence_generic", "reason": "explicit override accepted a generic/platform/feature term"})
                accepted.append(seed)
            else:
                warnings.append({"seed": seed, "code": "generic_seed_rejected", "reason": "isolated generic, platform, or feature-jargon seeds require allow_generic_seeds=true"})
                rejected.append(seed)
        else:
            accepted.append(seed)
    return accepted, warnings, rejected


def _require_seeds(seeds: list[str]) -> None:
    if not [s for s in seeds if s.strip()]:
        raise validation_error(
            "seeds is required and must be non-empty. " + _SEEDING_GUIDANCE,
            field_name="seeds",
        )


def _known_brand_names(brand_name: str | None, excluded_brand_terms: list[str] | None) -> list[str]:
    names = list(excluded_brand_terms or [])
    if brand_name:
        names.append(brand_name)
    return names


async def research_keywords(
    *,
    bundle_id: str,
    locale: str,
    seeds: list[str],
    target_genre_id: int | None = None,
    brand_name: str | None = None,
    excluded_brand_terms: list[str] | None = None,
    allow_generic_seeds: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if not is_valid_locale(locale):
        raise validation_error(
            f"unknown locale '{locale}'; use an App Store Connect locale id (for example 'ja' or 'ko'); "
            "common BCP-47 aliases ja-JP and ko-KR are accepted",
            field_name="locale",
        )
    _validate_target_genre_id(target_genre_id)
    _validate_seed_count(seeds)
    _require_seeds(seeds)
    accepted_seeds, warnings, rejected_inputs = _seed_validation(
        seeds, brand_name=brand_name, excluded_brand_terms=excluded_brand_terms,
        allow_generic_seeds=allow_generic_seeds,
    )
    if not accepted_seeds:
        raise validation_error("all seeds were rejected. " + _SEEDING_GUIDANCE, field_name="seeds")
    service = context.get_research_service()
    effective_target_genre_id = target_genre_id
    if effective_target_genre_id is None:
        effective_target_genre_id = await service.resolve_genre_id(bundle_id=bundle_id)
    request_fingerprint = _fingerprint({
        "bundle_id": bundle_id, "locale": locale, "seeds": sorted({s.casefold() for s in accepted_seeds}),
        "target_genre_id": effective_target_genre_id, "brand_name": brand_name,
        "excluded_brand_terms": sorted({s.casefold() for s in (excluded_brand_terms or [])}),
        "allow_generic_seeds": allow_generic_seeds,
    })
    seed_fingerprint = _fingerprint(sorted({s.casefold() for s in accepted_seeds}))
    if force_refresh:
        context.POOL_CACHE.delete(bundle_id=bundle_id, locale_id=locale)
    cached = None if force_refresh else context.POOL_CACHE.load(bundle_id=bundle_id, locale_id=locale, request_fingerprint=request_fingerprint)
    if cached is not None:
        cached = deepcopy(cached)
        _without_excluded_brands(cached, excluded_brand_terms)
        result = _research_observability(pool_to_dict(cached), request_fingerprint, seed_fingerprint, True, warnings, rejected_inputs, effective_target_genre_id, accepted_seeds)
        return pool_signing.sign(result)

    pool = await service.research(
        locale_id=locale,
        seeds=accepted_seeds,
        bundle_id=bundle_id,
        target_genre_id=effective_target_genre_id,
        known_brand_names=_known_brand_names(brand_name, excluded_brand_terms),
    )
    _without_excluded_brands(pool, excluded_brand_terms)
    if pool.has_evidence:
        context.POOL_CACHE.save(pool, bundle_id=bundle_id, request_fingerprint=request_fingerprint)
    result = _research_observability(pool_to_dict(pool), request_fingerprint, seed_fingerprint, False, warnings, rejected_inputs, effective_target_genre_id, accepted_seeds)
    diagnostics = service.last_diagnostics
    failure_counts = diagnostics.failure_counts if diagnostics is not None else {}
    throttle_signals = sum(failure_counts.get(key, 0) for key in ("rate_limited", "http_429", "http_403"))
    tenant_id = context.resolve_tenant_id()
    if throttle_signals >= 2 and tenant_id != context.LOCAL_TENANT_ID:
        # The signed plan is intentionally bounded to eight accepted seeds. Keep the original
        # pool intact and let the client recover the highest-priority portion locally.
        plan_seeds = accepted_seeds[: local_research_plan.MAX_HINT_SEED_REQUESTS]
        plan_fingerprint = _fingerprint({"request_fingerprint": request_fingerprint, "seeds": plan_seeds})
        plan = local_research_plan.issue_research_plan(
            tenant_id=tenant_id, bundle_id=bundle_id, locale=locale,
            accepted_seeds=plan_seeds, target_genre_id=effective_target_genre_id,
            brand_name=brand_name, excluded_brand_terms=list(excluded_brand_terms or []),
            allow_generic_seeds=allow_generic_seeds, request_fingerprint=plan_fingerprint,
        )
        result["local_research_fallback"] = {
            "recommended": True,
            "reason": "Repeated public Apple throttling signals limited hosted research.",
            "setup_required": False,
            "instructions": (
                "No new MCP, local installation, or credentials are needed. If you have existing "
                "local terminal, browser, or web-request capabilities, use them to execute the "
                "signed plan's bounded public Apple requests and submit the response-derived "
                "evidence to asogenic_complete_local_research. Otherwise continue with this partial result."
            ),
            "plan": plan,
        }
    return pool_signing.sign(result)


async def complete_local_research(*, plan: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Score bounded client-collected public Apple evidence and return a signed KeywordPool."""
    verified = local_research_plan.verify_research_plan(
        plan, tenant_id=context.resolve_tenant_id(),
    )
    request = verified["request"]
    pool = await evidence_research.research_from_evidence(
        locale_id=request["locale"], seeds=request["accepted_seeds"],
        target_genre_id=request["target_genre_id"], bundle_id=request["bundle_id"],
        known_brand_names=_known_brand_names(request["brand_name"], request["excluded_brand_terms"]),
        evidence=evidence, date_provider=lambda: datetime.now(UTC),
    )
    _without_excluded_brands(pool, request["excluded_brand_terms"])
    if pool.has_evidence:
        context.POOL_CACHE.save(
            pool, bundle_id=request["bundle_id"],
            request_fingerprint=request["request_fingerprint"],
        )
    seed_fingerprint = _fingerprint(sorted({s.casefold() for s in request["accepted_seeds"]}))
    result = _research_observability(
        pool_to_dict(pool), request["request_fingerprint"], seed_fingerprint,
        False, [], [], request["target_genre_id"], request["accepted_seeds"],
    )
    result["source"] = "client_collected_evidence"
    result["plan_id"] = verified["plan_id"]
    return pool_signing.sign(result)


def _research_observability(result: dict[str, Any], request_fingerprint: str, seed_fingerprint: str,
                            cache_hit: bool, warnings: list[dict[str, str]], rejected_inputs: list[str],
                            target_genre_id: int | None, accepted_seeds: list[str] | None = None) -> dict[str, Any]:
    pool = result.get("pool", result)
    terms = pool.get("terms", [])
    evidence = [t for t in terms if t.get("sources") and any(s in {"autocomplete", "competitor", "sourceLocale"} for s in t["sources"])]
    seed_evidence = [t for t in terms if "seed" in t.get("sources", [])]
    seed_keys = {re.sub(r"[^\w]+", " ", seed.casefold()).strip() for seed in (accepted_seeds or [])}
    covered_seed_keys = {
        seed
        for seed in seed_keys
        if any(
            seed == re.sub(r"[^\w]+", " ", str(term.get("term", "")).casefold()).strip()
            or seed in re.sub(r"[^\w]+", " ", str(term.get("term", "")).casefold()).strip()
            or re.sub(r"[^\w]+", " ", str(term.get("term", "")).casefold()).strip() in seed
            for term in seed_evidence
        )
    }
    category_evidence_status = "matched" if pool.get("category_name") else "no_matching_evidence"
    if target_genre_id is None and category_evidence_status == "matched":
        category_evidence_status = "resolved_from_market"
        warnings = [*warnings, {
            "code": "target_genre_unresolved",
            "reason": "no target genre was supplied and the app has no public listing to resolve one; category evidence is market-derived and should not be treated as authoritative",
        }]
    if target_genre_id is not None and category_evidence_status == "no_matching_evidence":
        warnings = [*warnings, {
            "code": "target_genre_no_matching_evidence",
            "reason": "the requested App Store genre was applied, but the researched results contained no matching category evidence",
        }]
    if not terms:
        warnings = [*warnings, {
            "code": "no_usable_localized_terms",
            "reason": "market/category context was found, but no usable localized keyword terms were produced for the requested seeds",
        }]
    if terms and pool.get("grounding") == "none":
        warnings = [*warnings, {
            "code": "no_grounding",
            "reason": "returned terms did not meet the evidence bar for grounded localized research",
        }]
    elif terms and pool.get("grounding") == "weak":
        warnings = [*warnings, {
            "code": "weak_grounding",
            "reason": "the locale produced terms, but the available market evidence is below the full grounding bar; treat suggestions as exploratory",
        }]
    if terms and not evidence:
        warnings = [*warnings, {
            "code": "zero_seed_coverage",
            "reason": "the locale produced no autocomplete or market-evidence terms; verify that seeds use the storefront's script and language",
        }]
    if seed_keys and not covered_seed_keys:
        warnings = [*warnings, {
            "code": "zero_seed_coverage",
            "reason": "none of the accepted seeds survived as localized evidence; the returned terms are not seed-grounded",
        }]
    elif seed_keys and len(covered_seed_keys) < len(seed_keys):
        warnings = [*warnings, {
            "code": "partial_seed_coverage",
            "reason": f"only {len(covered_seed_keys)} of {len(seed_keys)} accepted seeds produced localized evidence",
        }]
    report = result.get("report")
    if isinstance(report, dict) and not result.get("keywords"):
        warnings = [*warnings, {
            "code": "empty_keyword_field",
            "reason": "publish-field assembly produced no keywords; inspect report.dropped and the research warnings before treating the result as publishable",
        }]
        if terms:
            warnings.append({
                "code": "all_candidates_rejected",
                "reason": "researched terms were available but all candidates were rejected during publish-field assembly; inspect report.dropped for the applicable style, duplicate, or relevance reasons",
            })
    grounding = pool.get("grounding")
    category_confidence = 1.0 if pool.get("category_name") and grounding == "full" else 0.5 if pool.get("category_name") else 0.0
    result.update({
        **release_info(),
        "request_fingerprint": request_fingerprint, "seed_fingerprint": seed_fingerprint,
        "cache_hit": cache_hit, "cache_key_version": "research-request-v2",
        "applied_target_genre_id": target_genre_id,
        "category_evidence_status": category_evidence_status,
        "category_confidence": category_confidence,
        "locale_coverage": 1.0 if pool.get("locale_id") else 0.0,
        "seed_coverage": len(covered_seed_keys) / len(seed_keys) if seed_keys else 0.0,
        "warnings": warnings, "rejected_inputs": rejected_inputs,
        "selected_brand_terms": [t["term"] for t in terms if t.get("is_brand_term")],
        "grounding_explanation": ["market evidence present"] if evidence and pool.get("grounding") != "none" else [],
    })
    return result


def _without_excluded_brands(pool: Any, excluded_brand_terms: list[str] | None) -> None:
    excluded = {re.sub(r"[^\w]+", " ", value.casefold()).strip() for value in (excluded_brand_terms or [])}
    excluded_parts = {
        part
        for value in excluded
        for part in value.split()
        if part
    }
    if excluded:
        pool.terms[:] = [
            t for t in pool.terms
            if re.sub(r"[^\w]+", " ", t.term.casefold()).strip() not in excluded
            and re.sub(r"[^\w]+", " ", t.term.casefold()).strip() not in excluded_parts
        ]


async def resolve_genre(*, bundle_id: str) -> dict[str, Any]:
    service = context.get_research_service()
    genre_id = await service.resolve_genre_id(bundle_id=bundle_id)
    if genre_id is None:
        return {
            "genre_id": None,
            "warning": "No public App Store listing was found; genre resolution only works for shipped apps and cannot distinguish an unpublished app from an unknown genre.",
        }
    return {"genre_id": genre_id}


def assemble_locale(
    *,
    pool: dict[str, Any],
    name_candidates: list[str],
    subtitle_candidates: list[str],
    current_name: str,
    current_subtitle: str,
    current_keywords: str = "",
    category_name: str | None = None,
    brand_name: str | None = None,
    excluded_brand_terms: list[str] | None = None,
) -> dict[str, Any]:
    verified_pool = pool_signing.verify(pool)
    pool_obj = pool_from_dict(verified_pool)
    identity = metadata_assembler.assemble_identity(
        pool=pool_obj,
        name_candidates=name_candidates,
        subtitle_candidates=subtitle_candidates,
        current_name=current_name,
        current_subtitle=current_subtitle,
    )
    keywords, report = metadata_assembler.assemble_keywords(
        pool=pool_obj,
        name=identity.name,
        subtitle=identity.subtitle,
        category_name=category_name,
        brand_name=brand_name,
        excluded_brand_terms=frozenset(excluded_brand_terms or []),
    )
    result = {
        "name": identity.name,
        "subtitle": identity.subtitle,
        "keywords": keywords,
        "report": coverage_report_to_dict(report),
    }
    warnings = list(verified_pool.get("warnings", []))
    if not keywords and pool_obj.grounding.value == "none":
        warnings.append({
            "code": "no_grounding",
            "reason": "the supplied research pool has no grounded localized terms, so no publishable keyword field was assembled",
        })
    if warnings:
        result["warnings"] = warnings
    return result


def list_brand_candidates(*, pool: dict[str, Any]) -> list[dict[str, Any]]:
    """The pool's top-ranked apps whose brand became a keyword term — the practical
    "brand-candidate" list once research has already run (KeywordPool only persists the
    top-20 ranked apps, not TopAppRanking's full 60-deep brand-candidate list)."""
    pool_obj = pool_from_dict(pool_signing.verify(pool))
    brand_terms = {t.term for t in pool_obj.terms if t.is_brand_term}
    return [ranked_app_to_dict(app) for app in pool_obj.top_apps if app.brand in brand_terms]


def edit_keyword_field(
    *,
    field: str,
    term: str,
    action: str,
    style: str,
    covered_elsewhere: list[str] | None = None,
    is_brand_term: bool = False,
    is_autocomplete_confirmed: bool = False,
) -> dict[str, Any]:
    try:
        style_enum = KeywordFieldStyle(style)
    except ValueError as exc:
        valid = ", ".join(s.value for s in KeywordFieldStyle)
        raise validation_error(f"unknown style '{style}' (expected one of: {valid})", field_name="style") from exc
    if action == "insert":
        outcome = keyword_field_editor.insert(
            term,
            field=field,
            style=style_enum,
            covered_elsewhere=frozenset(covered_elsewhere or []),
            is_brand_term=is_brand_term,
            is_autocomplete_confirmed=is_autocomplete_confirmed,
        )
        if isinstance(outcome, Inserted):
            return {"field": outcome.field}
        return {"rejection_reason": outcome.reason.value}
    if action == "remove":
        return {"field": keyword_field_editor.remove(term, field=field)}
    raise validation_error(
        f"unknown action '{action}' (expected 'insert' or 'remove')", field_name="action"
    )


async def optimize_source_search(
    *,
    bundle_id: str,
    source_locale: str,
    current: dict[str, Any],
    seeds: list[str],
    target_genre_id: int | None = None,
    brand_name: str | None = None,
    excluded_brand_terms: list[str] | None = None,
    force_refresh: bool = False,
    allow_generic_seeds: bool = False,
) -> dict[str, Any]:
    """Deterministic assembly for the source listing itself, per
    docs/use-cases/07-source-optimization.md.

    `target_genre_id` is the client's authoritative App Store genre when known. If omitted,
    the genre is resolved from the app listing. `seeds` is REQUIRED. Provide real end-user search phrases for this app's
    job-to-be-done: the 2-4 word queries a user would actually type
    ("menopause symptom tracker", "hrt reminder", "hot flashes diary"). Do NOT seed
    with isolated generic words ("doctor", "reports", "tracker", "free"), the brand/app
    name, competitor names, or feature jargon no one searches - each seed fans out
    through Apple autocomplete, so an off-target seed pulls a whole branch of unrelated
    results into the pool. 4-12 well-chosen phrases beat 25 loose ones. Read `current`
    (name/subtitle/description) and the app's actual purpose to choose them; the current
    listing wording is a starting point, not a seed list."""
    if not is_valid_locale(source_locale):
        raise validation_error(
            f"unknown locale '{source_locale}'; use an App Store Connect locale id (for example 'ja' or 'ko'); "
            "common BCP-47 aliases ja-JP and ko-KR are accepted",
            field_name="source_locale",
        )
    _known_fields = {
        "name", "subtitle", "keywords", "promotional_text", "description", "whats_new",
        "marketing_url", "support_url", "privacy_policy_url",
    }
    current_content = MetadataLocalization(**{k: v for k, v in current.items() if k in _known_fields})

    _require_seeds(seeds)
    _validate_seed_count(seeds)
    accepted_seeds, warnings, rejected_inputs = _seed_validation(
        seeds, brand_name=brand_name, excluded_brand_terms=excluded_brand_terms,
        allow_generic_seeds=allow_generic_seeds,
    )
    if not accepted_seeds:
        raise validation_error("all seeds were rejected. " + _SEEDING_GUIDANCE, field_name="seeds")
    service = context.get_research_service()
    effective_target_genre_id = target_genre_id if target_genre_id is not None else await service.resolve_genre_id(bundle_id=bundle_id)
    request_fingerprint = _fingerprint({"bundle_id": bundle_id, "locale": source_locale, "seeds": sorted({s.casefold() for s in accepted_seeds}), "target_genre_id": effective_target_genre_id, "brand_name": brand_name, "excluded_brand_terms": sorted({s.casefold() for s in (excluded_brand_terms or [])}), "allow_generic_seeds": allow_generic_seeds})
    seed_fingerprint = _fingerprint(sorted({s.casefold() for s in accepted_seeds}))

    if force_refresh:
        context.POOL_CACHE.delete(bundle_id=bundle_id, locale_id=source_locale)

    cached = None if force_refresh else context.POOL_CACHE.load(bundle_id=bundle_id, locale_id=source_locale, request_fingerprint=request_fingerprint)
    if cached is not None:
        pool_obj = deepcopy(cached)
    else:
        pool_obj = await service.research(
            locale_id=source_locale,
            seeds=accepted_seeds,
            bundle_id=bundle_id,
            target_genre_id=effective_target_genre_id,
            known_brand_names=_known_brand_names(brand_name, excluded_brand_terms),
        )
        _without_excluded_brands(pool_obj, excluded_brand_terms)
        if pool_obj.has_evidence:
            context.POOL_CACHE.save(pool_obj, bundle_id=bundle_id, request_fingerprint=request_fingerprint)
    _without_excluded_brands(pool_obj, excluded_brand_terms)

    identity = metadata_assembler.assemble_identity(
        pool=pool_obj,
        name_candidates=[current_content.name],
        subtitle_candidates=[current_content.subtitle],
        current_name=current_content.name,
        current_subtitle=current_content.subtitle,
    )
    # optimize_source_search proposes keywords; it is not a title rewrite operation. Preserve
    # existing non-empty listing fields even when an invalid/overlong value cannot participate in
    # identity scoring, instead of silently returning a destructive blank field.
    if current_content.name or current_content.subtitle:
        identity = metadata_assembler.IdentityResult(
            name=current_content.name or identity.name,
            subtitle=current_content.subtitle or identity.subtitle,
            covered=identity.covered,
            alternatives=identity.alternatives,
        )
    keywords, report = metadata_assembler.assemble_keywords(
        pool=pool_obj,
        name=identity.name,
        subtitle=identity.subtitle,
        brand_name=brand_name,
        excluded_brand_terms=frozenset(excluded_brand_terms or []),
    )
    result: dict[str, Any] = {
        "name": identity.name,
        "subtitle": identity.subtitle,
        "keywords": keywords,
        "report": coverage_report_to_dict(report),
        "pool": pool_to_dict(pool_obj),
    }
    result = _research_observability(result, request_fingerprint, seed_fingerprint, cached is not None, warnings, rejected_inputs, effective_target_genre_id, accepted_seeds)
    result["pool"].update({
        "request_fingerprint": request_fingerprint,
        "seed_fingerprint": seed_fingerprint,
        "cache_key_version": "research-request-v2",
    })
    result["pool"] = pool_signing.sign(result["pool"])
    return result


def get_conversion_guidance(*, locale: str, content: dict[str, Any]) -> dict[str, Any]:
    """Barred-content rules plus the current text, so the client can rewrite the description/
    promo text itself (that rewrite is AI and stays in the client)."""
    text = " ".join(str(content.get(key, "")) for key in ("name", "subtitle", "description", "promotional_text")).casefold()
    health_domain = any(marker in text for marker in _HEALTH_PATTERNS)
    domain_rules = [
        "flag and review diagnostic claims such as diagnosing causes, identifying conditions, or providing clinical answers",
        "do not guarantee symptom relief, treatment outcomes, or weight-loss results",
        "do not imply medication changes or that the app replaces a clinician or medical advice",
        "preserve existing medical disclaimers and require human review when rewriting health claims",
    ] if health_domain else []
    claim_warnings = []
    claim_markers = {
        "diagnos": "diagnostic claim", "guarantee": "guarantee claim", "symptom relief": "symptom-relief claim",
        "clinical": "clinical claim", "replaces medical advice": "replacement-of-care claim",
        "replace medical advice": "replacement-of-care claim", "glp-1": "medication claim", "weight loss": "weight-loss claim",
    }
    for marker, label in claim_markers.items():
        start = text.find(marker)
        if start >= 0:
            claim_warnings.append({"claim": label, "source_span": [start, start + len(marker)], "reason": "requires domain review"})
    return {
        **release_info(),
        "locale": locale,
        "barred_rules": BARRED_CONTENT_RULES,
        "domain": "health_medical" if health_domain else "general",
        "domain_rules": domain_rules,
        "claim_warnings": claim_warnings,
        "requires_disclaimer_review": health_domain,
        "current_description": content.get("description", ""),
        "current_promo": content.get("promotional_text", ""),
    }
