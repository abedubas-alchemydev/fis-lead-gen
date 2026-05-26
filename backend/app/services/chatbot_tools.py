"""Doxie chatbot tool registry — read-only lookups against BD / IA / II repos.

Each tool is a thin wrapper around an existing repository method, gated on
the matching per-user feature permission. Tools are dispatched by
``ChatbotService`` during Gemini function-calling; results come back as
JSON-able dicts that Gemini incorporates into its reply.

Design rules followed throughout this module:

- Tools **never raise** into the Gemini iteration loop. Every expected
  failure (permission denied, bad args, missing row, repo exception) is
  caught and returned as a structured ``{"error": "...", "message": "..."}``
  dict. The model gets to surface it gracefully ("you don't have access to
  that data") instead of the whole chat 502'ing.
- Tool results are **compact hand-projected dicts**, not full Pydantic
  ``model_dump()`` payloads. A search returning 10 firms × 30 fields would
  eat the prompt budget; we cap to ~10 fields per summary row and ~22 per
  profile. The schema-drift guard in ``test_chatbot_tools.py`` catches
  accidental key renames on the underlying Pydantic schemas.
- All projections pass through ``_jsonable`` so ``Decimal`` / ``date`` /
  ``datetime`` values get pre-serialized before httpx tries to JSON-encode
  them — httpx's default encoder would otherwise raise ``TypeError``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_permissions import (
    INSTITUTIONAL_INVESTORS,
    INVESTMENT_ADVISORS,
    MASTER_LIST,
)
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import (
    BrokerDealerListItem,
    FinancialMetricItem,
)
from app.schemas.institutional_investor import InstitutionalInvestorListItem
from app.schemas.investment_advisor import InvestmentAdvisorListItem
from app.schemas.pipeline import ClearingArrangementItem
from app.services.auth import ensure_feature
from app.services.broker_dealers import BrokerDealerRepository
from app.services.chatbot_semantic import (
    ChatbotSemanticService,
    ENTITY_TYPE_BROKER_DEALER,
)
from app.services.institutional_investors import InstitutionalInvestorRepository
from app.services.investment_advisors import InvestmentAdvisorRepository

logger = logging.getLogger(__name__)

# Caps. Kept as module constants so the schema-drift test and the tool
# descriptions can reference the same numbers.
PROFILE_FINANCIALS_LIMIT = 3
PROFILE_CLEARING_LIMIT = 3
SEARCH_RESULT_LIMIT_MAX = 10
SEARCH_RESULT_LIMIT_DEFAULT = 5
ADVISORY_LIST_CAP = 10
CLIENT_TYPE_LIST_CAP = 10
# list-by-filter tools get a slightly higher ceiling than the search tools
# because filters narrow the result space; returning 25 firms in CA matters
# more than returning 25 fuzzy name matches.
LIST_FILTER_LIMIT_DEFAULT = 10
LIST_FILTER_LIMIT_MAX = 25


@dataclass(frozen=True)
class Tool:
    """One tool that Gemini can call.

    ``execute`` takes the calling user (for ``ensure_feature``), an
    ``AsyncSession`` (the endpoint injects one per request), and the
    arg dict Gemini sent in the ``functionCall`` part. Returns the
    JSON-able result dict that becomes the ``functionResponse`` payload.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]
    feature_key: str
    execute: Callable[
        [AuthenticatedUser, AsyncSession, Mapping[str, Any]],
        Awaitable[dict[str, Any]],
    ]


# ── Helpers ──────────────────────────────────────────────────────────────


def _jsonable(value: Any) -> Any:
    """Recursively coerce Decimal / date / datetime to JSON-friendly types.

    httpx's default JSON encoder rejects ``Decimal`` and ``date`` — we'd
    rather pay a small projection cost here than discover that at runtime
    when Gemini's payload fails to serialize.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _check_feature(user: AuthenticatedUser, feature_key: str) -> dict[str, Any] | None:
    """Return an error dict if the user lacks the feature, else None."""
    try:
        ensure_feature(user, feature_key)
    except HTTPException as exc:
        if exc.status_code == 403:
            return {
                "error": "no_access",
                "message": (
                    f"User does not have access to the '{feature_key}' "
                    f"feature. Tell them they need that permission granted "
                    f"to use this lookup."
                ),
            }
        # Anything else from ensure_feature is unexpected — bubble it up to
        # the loop's generic catch-all rather than swallowing silently.
        raise
    return None


def _clamp_limit(raw: Any) -> int:
    try:
        n = int(raw) if raw is not None else SEARCH_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        n = SEARCH_RESULT_LIMIT_DEFAULT
    return max(1, min(n, SEARCH_RESULT_LIMIT_MAX))


def _clamp_filter_limit(raw: Any) -> int:
    """Clamp helper for the list-by-filter tools (larger ceiling than search)."""
    try:
        n = int(raw) if raw is not None else LIST_FILTER_LIMIT_DEFAULT
    except (TypeError, ValueError):
        n = LIST_FILTER_LIMIT_DEFAULT
    return max(1, min(n, LIST_FILTER_LIMIT_MAX))


def _opt_str(args: Mapping[str, Any], key: str) -> str | None:
    """Return a trimmed string arg or None for missing / empty values."""
    raw = args.get(key)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _opt_float(args: Mapping[str, Any], key: str) -> float | None:
    raw = args.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _opt_bool(args: Mapping[str, Any], key: str) -> bool | None:
    raw = args.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True
        if lowered in ("false", "no", "n", "0"):
            return False
    return None


def _require_query(args: Mapping[str, Any]) -> str | dict[str, Any]:
    """Return the trimmed query string or an ``error`` dict."""
    raw = args.get("query")
    query = str(raw).strip() if raw is not None else ""
    if not query:
        return {
            "error": "invalid_args",
            "message": "Argument 'query' is required and must be non-empty.",
        }
    return query


# ── Projections ──────────────────────────────────────────────────────────
#
# Keys returned here are referenced by ``test_chatbot_tools.py``'s schema-
# drift guard — every key in the projection must exist as a field on the
# underlying Pydantic schema (``BrokerDealerListItem`` / ``InvestmentAdvisorListItem``).
# If you rename a schema field, rename the projection key in lockstep.

_BD_SUMMARY_KEYS = (
    "id",
    "name",
    "crd_number",
    "cik",
    "city",
    "state",
    "status",
    "lead_priority",
    "current_clearing_partner",
    "latest_net_capital",
)


_BD_PROFILE_EXTRA_KEYS = (
    "three_year_cagr",
    "yoy_growth",
    "is_deficient",
    "registration_date",
    "last_filing_date",
    "latest_total_assets",
    "current_clearing_type",
    "health_status",
    "website",
)


_IA_SUMMARY_KEYS = (
    "id",
    "name",
    "crd_number",
    "cik",
    "city",
    "state",
    "status",
    "regulatory_aum",
    "files_13f",
    "total_clients",
)


_IA_PROFILE_EXTRA_KEYS = (
    "legal_name",
    "discretionary_aum",
    "non_discretionary_aum",
    "registration_date",
    "last_filing_date",
    "latest_13f_filing_date",
    "website",
)


_II_SUMMARY_KEYS = (
    "id",
    "name",
    "cik",
    "city",
    "state",
    "status",
    "total_aum",
    "holdings_count",
    "latest_13f_filing_date",
    "advisor_id",
)


_II_PROFILE_EXTRA_KEYS = (
    "legal_name",
    "website",
    "filings_index_url",
)


def _project_bd_summary(item: BrokerDealerListItem) -> dict[str, Any]:
    return _jsonable({k: getattr(item, k, None) for k in _BD_SUMMARY_KEYS})


def _project_bd_profile(
    item: BrokerDealerListItem,
    financials: list[FinancialMetricItem],
    arrangements: list[ClearingArrangementItem],
) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _BD_SUMMARY_KEYS}
    for k in _BD_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    out["latest_financials"] = [
        {
            "report_date": f.report_date,
            "net_capital": f.net_capital,
            "excess_net_capital": f.excess_net_capital,
            "total_assets": f.total_assets,
            "required_min_capital": f.required_min_capital,
        }
        for f in financials[:PROFILE_FINANCIALS_LIMIT]
    ]
    out["clearing_arrangements"] = [
        {
            "filing_year": a.filing_year,
            "report_date": a.report_date,
            "clearing_partner": a.clearing_partner,
            "clearing_type": a.clearing_type,
            "is_competitor": a.is_competitor,
        }
        for a in arrangements[:PROFILE_CLEARING_LIMIT]
    ]
    return _jsonable(out)


def _project_ia_summary(item: InvestmentAdvisorListItem) -> dict[str, Any]:
    return _jsonable({k: getattr(item, k, None) for k in _IA_SUMMARY_KEYS})


def _project_ia_profile(item: InvestmentAdvisorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _IA_SUMMARY_KEYS}
    for k in _IA_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    # Cap multi-select arrays so a verbose ADV doesn't blow the prompt.
    activities = item.advisory_activities or []
    out["advisory_activities"] = list(activities[:ADVISORY_LIST_CAP])
    client_types = item.client_types or []
    out["client_types"] = list(client_types[:CLIENT_TYPE_LIST_CAP])
    return _jsonable(out)


def _project_ii_summary(item: InstitutionalInvestorListItem) -> dict[str, Any]:
    return _jsonable({k: getattr(item, k, None) for k in _II_SUMMARY_KEYS})


def _project_ii_profile(item: InstitutionalInvestorListItem) -> dict[str, Any]:
    out: dict[str, Any] = {k: getattr(item, k, None) for k in _II_SUMMARY_KEYS}
    for k in _II_PROFILE_EXTRA_KEYS:
        out[k] = getattr(item, k, None)
    return _jsonable(out)


# ── Tool execute functions ───────────────────────────────────────────────


_bd_repo = BrokerDealerRepository()
_ia_repo = InvestmentAdvisorRepository()
_ii_repo = InstitutionalInvestorRepository()
_semantic_service = ChatbotSemanticService()


# Semantic search returns at most this many hits regardless of what
# Gemini asks for. Higher caps balloon the prompt budget; lower caps
# make Doxie miss good matches that ranked just outside the window.
SEMANTIC_RESULT_LIMIT_MAX = 8
SEMANTIC_RESULT_LIMIT_DEFAULT = 5


async def _execute_search_broker_dealers(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _bd_repo.list_broker_dealers(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            health_statuses=[],
            lead_priorities=[],
            clearing_partners=[],
            clearing_types=[],
            types_of_business=[],
            list_mode="all",
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception("doxie tool failed", extra={"tool": "search_broker_dealers"})
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_bd_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


async def _execute_get_broker_dealer_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    try:
        bd_id = int(args.get("broker_dealer_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'broker_dealer_id' must be an integer.",
        }
    try:
        bd = await _bd_repo.get_broker_dealer(db, bd_id)
        if bd is None:
            return {
                "error": "not_found",
                "message": f"No broker-dealer with id={bd_id}.",
            }
        financials = await _bd_repo.get_financial_metrics(db, bd_id)
        arrangements = await _bd_repo.list_clearing_arrangements(db, bd_id)
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "get_broker_dealer_profile"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    # ``from_attributes=True`` on each schema lets us validate the ORM
    # instances directly into Pydantic shapes for projection.
    bd_item = BrokerDealerListItem.model_validate(bd)
    financial_items = [FinancialMetricItem.model_validate(f) for f in financials]
    arrangement_items = [
        ClearingArrangementItem.model_validate(a) for a in arrangements
    ]
    return _project_bd_profile(bd_item, financial_items, arrangement_items)


async def _execute_search_investment_advisors(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _ia_repo.list_investment_advisors(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            advisory_activities=[],
            client_types=[],
            # ``files_13f=None`` disables the hard 13F scope the master-list
            # endpoint defaults on — Doxie shouldn't silently hide non-13F
            # advisors from a name search.
            files_13f=None,
            min_regulatory_aum=None,
            max_regulatory_aum=None,
            registered_after=None,
            registered_before=None,
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "search_investment_advisors"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_ia_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


async def _execute_get_investment_advisor_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial
    try:
        advisor_id = int(args.get("advisor_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'advisor_id' must be an integer.",
        }
    try:
        advisor = await _ia_repo.get_investment_advisor(db, advisor_id)
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "get_investment_advisor_profile"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if advisor is None:
        return {
            "error": "not_found",
            "message": f"No investment advisor with id={advisor_id}.",
        }
    item = InvestmentAdvisorListItem.model_validate(advisor)
    return _project_ia_profile(item)


async def _execute_search_institutional_investors(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INSTITUTIONAL_INVESTORS)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    limit = _clamp_limit(args.get("limit"))
    try:
        response = await _ii_repo.list_institutional_investors(
            db,
            search=query_or_error,
            states=[],
            statuses=[],
            min_total_aum=None,
            max_total_aum=None,
            filed_13f_after=None,
            filed_13f_before=None,
            only_with_advisor_link=None,
            sort_by="name",
            sort_dir="asc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "search_institutional_investors"}
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_ii_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


async def _execute_get_institutional_investor_profile(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    denial = _check_feature(user, INSTITUTIONAL_INVESTORS)
    if denial is not None:
        return denial
    try:
        investor_id = int(args.get("investor_id"))
    except (TypeError, ValueError):
        return {
            "error": "invalid_args",
            "message": "Argument 'investor_id' must be an integer.",
        }
    try:
        investor = await _ii_repo.get_institutional_investor(db, investor_id)
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "get_institutional_investor_profile"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    if investor is None:
        return {
            "error": "not_found",
            "message": f"No institutional investor with id={investor_id}.",
        }
    item = InstitutionalInvestorListItem.model_validate(investor)
    return _project_ii_profile(item)


async def _execute_list_broker_dealers_by_filter(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Criteria-style BD list (state, status, clearing partner, etc.).

    Distinct from ``search_broker_dealers`` (which is fuzzy-name-match):
    this tool takes structured filters and returns the firms that satisfy
    *all* of them. At least one filter must be set — an unfiltered query
    is just a name-less search, and Gemini should call the search tool
    for that.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial

    state = _opt_str(args, "state")
    status = _opt_str(args, "status")
    clearing_partner = _opt_str(args, "clearing_partner")
    lead_priority = _opt_str(args, "lead_priority")
    min_net_capital = _opt_float(args, "min_net_capital")
    max_net_capital = _opt_float(args, "max_net_capital")

    any_filter_set = any(
        v is not None
        for v in (
            state,
            status,
            clearing_partner,
            lead_priority,
            min_net_capital,
            max_net_capital,
        )
    )
    if not any_filter_set:
        return {
            "error": "invalid_args",
            "message": (
                "At least one filter is required. Use search_broker_dealers "
                "for fuzzy name lookups instead."
            ),
        }

    limit = _clamp_filter_limit(args.get("limit"))
    try:
        response = await _bd_repo.list_broker_dealers(
            db,
            search=None,
            states=[state] if state else [],
            statuses=[status] if status else [],
            health_statuses=[],
            lead_priorities=[lead_priority] if lead_priority else [],
            clearing_partners=[clearing_partner] if clearing_partner else [],
            clearing_types=[],
            types_of_business=[],
            list_mode="all",
            sort_by="latest_net_capital",
            sort_dir="desc",
            page=1,
            limit=limit,
            min_net_capital=min_net_capital,
            max_net_capital=max_net_capital,
        )
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "list_broker_dealers_by_filter"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_bd_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


async def _execute_semantic_firm_search(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Conceptual / semantic firm lookup via the RAG embedding index.

    Distinct from ``search_broker_dealers`` (substring on name/CRD/CIK).
    Use when the user's query is descriptive — "broker-dealers
    specializing in retail HNW clients", "firms similar to Acme" —
    rather than a specific identifier or name fragment.
    """
    denial = _check_feature(user, MASTER_LIST)
    if denial is not None:
        return denial
    query_or_error = _require_query(args)
    if isinstance(query_or_error, dict):
        return query_or_error
    try:
        raw_limit = int(args["limit"]) if args.get("limit") is not None else SEMANTIC_RESULT_LIMIT_DEFAULT
    except (TypeError, ValueError):
        raw_limit = SEMANTIC_RESULT_LIMIT_DEFAULT
    limit = max(1, min(raw_limit, SEMANTIC_RESULT_LIMIT_MAX))

    try:
        hits = await _semantic_service.search(
            db,
            query=query_or_error,
            entity_types=[ENTITY_TYPE_BROKER_DEALER],
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed", extra={"tool": "semantic_firm_search"}
        )
        return {
            "error": "tool_error",
            "message": (
                "Semantic search failed; the index may not be populated yet. "
                "Ask the user to try a name-based search instead."
            ),
        }

    if not hits:
        return {
            "items": [],
            "total_matched": 0,
            "note": (
                "No semantic matches found. The embedding index may not be "
                "populated; an admin can backfill it from settings."
            ),
        }

    # Fetch the BD rows for each hit so Doxie has names + identifiers to
    # cite. The hit's ``content`` snippet is what the embedding actually
    # matched — surfacing it lets Doxie quote the relevant phrase.
    bd_ids = [h.entity_id for h in hits if h.entity_type == ENTITY_TYPE_BROKER_DEALER]
    items: list[dict[str, Any]] = []
    for hit in hits:
        if hit.entity_type != ENTITY_TYPE_BROKER_DEALER:
            continue
        try:
            bd = await _bd_repo.get_broker_dealer(db, hit.entity_id)
        except Exception:
            logger.exception(
                "doxie tool failed loading bd id=%s tool=semantic_firm_search",
                hit.entity_id,
            )
            continue
        if bd is None:
            # Embedding row points at a deleted BD — stale index entry.
            # Skip silently; a re-backfill will clean it up.
            continue
        bd_item = BrokerDealerListItem.model_validate(bd)
        summary = _project_bd_summary(bd_item)
        summary["similarity"] = round(hit.similarity, 4)
        summary["match_snippet"] = hit.content[:200]
        items.append(summary)

    return {
        "items": items,
        "total_matched": len(items),
        # The total we *would have* returned if not capped by feature
        # gates or stale-row filtering. Helpful for Doxie to mention
        # when the result set is truncated.
        "candidates_considered": len(bd_ids),
    }


async def _execute_list_investment_advisors_by_filter(
    user: AuthenticatedUser,
    db: AsyncSession,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Criteria-style IA list (state, status, AUM band, 13F flag).

    Distinct from ``search_investment_advisors``. Requires at least one
    filter so Gemini doesn't burn tokens enumerating a whole table.
    """
    denial = _check_feature(user, INVESTMENT_ADVISORS)
    if denial is not None:
        return denial

    state = _opt_str(args, "state")
    status = _opt_str(args, "status")
    min_regulatory_aum = _opt_float(args, "min_regulatory_aum")
    max_regulatory_aum = _opt_float(args, "max_regulatory_aum")
    files_13f = _opt_bool(args, "files_13f")

    any_filter_set = any(
        v is not None
        for v in (state, status, min_regulatory_aum, max_regulatory_aum, files_13f)
    )
    if not any_filter_set:
        return {
            "error": "invalid_args",
            "message": (
                "At least one filter is required. Use search_investment_advisors "
                "for fuzzy name lookups instead."
            ),
        }

    limit = _clamp_filter_limit(args.get("limit"))
    try:
        response = await _ia_repo.list_investment_advisors(
            db,
            search=None,
            states=[state] if state else [],
            statuses=[status] if status else [],
            advisory_activities=[],
            client_types=[],
            files_13f=files_13f,
            min_regulatory_aum=min_regulatory_aum,
            max_regulatory_aum=max_regulatory_aum,
            registered_after=None,
            registered_before=None,
            sort_by="regulatory_aum",
            sort_dir="desc",
            page=1,
            limit=limit,
        )
    except Exception:
        logger.exception(
            "doxie tool failed",
            extra={"tool": "list_investment_advisors_by_filter"},
        )
        return {
            "error": "tool_error",
            "message": "Lookup failed; ask the user to try again.",
        }
    return {
        "items": [_project_ia_summary(item) for item in response.items],
        "total_matched": response.meta.total,
    }


# ── Tool declarations ────────────────────────────────────────────────────

_SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Firm name, CRD number, CIK, or SEC file number "
                "(substring match)."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": SEARCH_RESULT_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to "
                f"{SEARCH_RESULT_LIMIT_DEFAULT}, capped at "
                f"{SEARCH_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": ["query"],
}

_BD_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "broker_dealer_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_broker_dealers."
            ),
        },
    },
    "required": ["broker_dealer_id"],
}

_IA_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "advisor_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_investment_advisors."
            ),
        },
    },
    "required": ["advisor_id"],
}


_II_PROFILE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "investor_id": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Internal numeric id returned by search_institutional_investors."
            ),
        },
    },
    "required": ["investor_id"],
}


_BD_LIST_FILTER_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "Two-letter US state code (e.g. 'NY', 'CA').",
        },
        "status": {
            "type": "string",
            "description": "FINRA registration status. Common values: 'active', 'inactive'.",
        },
        "clearing_partner": {
            "type": "string",
            "description": (
                "Canonical clearing-partner short name (e.g. 'Pershing', "
                "'Apex', 'BNY Mellon')."
            ),
        },
        "lead_priority": {
            "type": "string",
            "description": "Lead-scoring band: 'hot', 'warm', or 'cold'.",
        },
        "min_net_capital": {
            "type": "number",
            "description": "Minimum latest_net_capital in dollars.",
        },
        "max_net_capital": {
            "type": "number",
            "description": "Maximum latest_net_capital in dollars.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": LIST_FILTER_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to {LIST_FILTER_LIMIT_DEFAULT}, "
                f"capped at {LIST_FILTER_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


_SEMANTIC_SEARCH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-form descriptive query — e.g. 'firms similar to "
                "Acme Securities', 'broker-dealers focused on high-net-"
                "worth retail clients'. Use only when the query is "
                "conceptual; for exact name/CRD lookups call "
                "search_broker_dealers instead."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": SEMANTIC_RESULT_LIMIT_MAX,
            "description": (
                f"Max hits to return. Defaults to "
                f"{SEMANTIC_RESULT_LIMIT_DEFAULT}, capped at "
                f"{SEMANTIC_RESULT_LIMIT_MAX}."
            ),
        },
    },
    "required": ["query"],
}


_IA_LIST_FILTER_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "Two-letter US state code (e.g. 'NY', 'CA').",
        },
        "status": {
            "type": "string",
            "description": "SEC registration status. Common values: 'active', 'inactive'.",
        },
        "min_regulatory_aum": {
            "type": "number",
            "description": "Minimum regulatory_aum in dollars.",
        },
        "max_regulatory_aum": {
            "type": "number",
            "description": "Maximum regulatory_aum in dollars.",
        },
        "files_13f": {
            "type": "boolean",
            "description": "If true, restrict to advisors that file Form 13F-HR.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": LIST_FILTER_LIMIT_MAX,
            "description": (
                f"Max rows to return. Defaults to {LIST_FILTER_LIMIT_DEFAULT}, "
                f"capped at {LIST_FILTER_LIMIT_MAX}."
            ),
        },
    },
    "required": [],
}


TOOL_REGISTRY: dict[str, Tool] = {
    "search_broker_dealers": Tool(
        name="search_broker_dealers",
        description=(
            "Search SEC-registered broker-dealer firms by name, CRD number, "
            "CIK, or SEC file number (substring match). Use when the user "
            "mentions a broker-dealer by name or identifier and you need a "
            "list of candidates."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_search_broker_dealers,
    ),
    "get_broker_dealer_profile": Tool(
        name="get_broker_dealer_profile",
        description=(
            "Fetch one broker-dealer's full profile by internal numeric ID. "
            "Returns registration, net capital, year-over-year growth, "
            "clearing arrangements, and the most recent financial periods. "
            "Call after search_broker_dealers once a specific firm is "
            "identified."
        ),
        parameters_schema=_BD_PROFILE_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_get_broker_dealer_profile,
    ),
    "search_investment_advisors": Tool(
        name="search_investment_advisors",
        description=(
            "Search SEC-registered investment advisors (Form ADV filers) by "
            "name, CRD, or CIK. Returns AUM and basic profile fields."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_search_investment_advisors,
    ),
    "get_investment_advisor_profile": Tool(
        name="get_investment_advisor_profile",
        description=(
            "Fetch one investment advisor's full profile by numeric ID. "
            "Returns AUM split (regulatory / discretionary / "
            "non-discretionary), advisory activities, client mix, and "
            "registration / filing dates."
        ),
        parameters_schema=_IA_PROFILE_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_get_investment_advisor_profile,
    ),
    "search_institutional_investors": Tool(
        name="search_institutional_investors",
        description=(
            "Search SEC Form 13F filers (institutional investors / asset "
            "managers) by name, legal name, or CIK. Returns total_aum, "
            "holdings_count, and basic profile fields. Use for any "
            "'who holds X' or 'who's the asset manager called Y' question."
        ),
        parameters_schema=_SEARCH_PARAMETERS_SCHEMA,
        feature_key=INSTITUTIONAL_INVESTORS,
        execute=_execute_search_institutional_investors,
    ),
    "get_institutional_investor_profile": Tool(
        name="get_institutional_investor_profile",
        description=(
            "Fetch one institutional investor's full profile by numeric ID. "
            "Returns total AUM, holdings count, latest 13F filing date, and "
            "the linked investment-advisor id when the investor is also "
            "registered as an IA."
        ),
        parameters_schema=_II_PROFILE_PARAMETERS_SCHEMA,
        feature_key=INSTITUTIONAL_INVESTORS,
        execute=_execute_get_institutional_investor_profile,
    ),
    "list_broker_dealers_by_filter": Tool(
        name="list_broker_dealers_by_filter",
        description=(
            "List broker-dealers matching criteria (state / status / "
            "clearing partner / lead priority / net capital band). At least "
            "one filter is required. Use for 'who clears through Pershing in "
            "California' or 'show me hot leads with net capital above $5M' "
            "style queries. Use search_broker_dealers (not this tool) when "
            "the user names a specific firm."
        ),
        parameters_schema=_BD_LIST_FILTER_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_list_broker_dealers_by_filter,
    ),
    "list_investment_advisors_by_filter": Tool(
        name="list_investment_advisors_by_filter",
        description=(
            "List investment advisors matching criteria (state / status / "
            "AUM band / files_13f flag). At least one filter is required. "
            "Use search_investment_advisors instead when the user names a "
            "specific firm."
        ),
        parameters_schema=_IA_LIST_FILTER_PARAMETERS_SCHEMA,
        feature_key=INVESTMENT_ADVISORS,
        execute=_execute_list_investment_advisors_by_filter,
    ),
    "semantic_firm_search": Tool(
        name="semantic_firm_search",
        description=(
            "Conceptual / semantic search over broker-dealer firms via "
            "vector embeddings. Use when the user's query is descriptive "
            "rather than naming a specific firm — examples: 'firms similar "
            "to Acme Securities', 'broker-dealers focused on retail HNW', "
            "'small BDs that clear self'. For exact name / CRD lookups, "
            "call search_broker_dealers instead. Returns hits with a "
            "similarity score and a snippet of the matched summary."
        ),
        parameters_schema=_SEMANTIC_SEARCH_PARAMETERS_SCHEMA,
        feature_key=MASTER_LIST,
        execute=_execute_semantic_firm_search,
    ),
}
