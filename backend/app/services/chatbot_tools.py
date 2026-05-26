"""Doxie chatbot tool registry — read-only lookups against the BD + IA repos.

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

from app.core.feature_permissions import INVESTMENT_ADVISORS, MASTER_LIST
from app.schemas.auth import AuthenticatedUser
from app.schemas.broker_dealer import (
    BrokerDealerListItem,
    FinancialMetricItem,
)
from app.schemas.investment_advisor import InvestmentAdvisorListItem
from app.schemas.pipeline import ClearingArrangementItem
from app.services.auth import ensure_feature
from app.services.broker_dealers import BrokerDealerRepository
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


# ── Tool execute functions ───────────────────────────────────────────────


_bd_repo = BrokerDealerRepository()
_ia_repo = InvestmentAdvisorRepository()


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
}
